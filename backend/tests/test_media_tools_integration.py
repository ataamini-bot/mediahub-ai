"""PostgreSQL admission and crash invariants; providers are always stubbed."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, func, select, update

from app.api.media_tools import ToolSubmit, router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.app_setting import ApplicationSetting
from app.models.media_tool import MediaToolRequest
from app.models.download_job import DownloadJob
from app.models.user import User, UserStatus
from app.services.application_settings import ApplicationSettingsService, SettingValidationError
from app.services.media_tool_config import configure_tool, read_config, public_config
from app.services.media_tool_files import folder
from app.services.media_tools import MediaToolError, owned, reconcile_restored_tools, submit, sweep_tools


@pytest_asyncio.fixture
async def records(tmp_path, monkeypatch):
    assert settings.app_env == "test", "Use a disposable test database"
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    async with AsyncSessionLocal() as db:
        users = [User(telegram_id=8_000_000_000_000 + uuid4().int % 100_000_000, first_name="Tools test") for _ in range(3)]
        db.add_all(users)
        await db.flush()
        for kind in ("music", "portrait"):
            await configure_tool(db, {"kind": kind, "enabled": True, "token": "test-private-token",
                "per_user_daily": 2, "global_daily": 10}, users[0])
        await db.commit()
    def payload(kind="music", user=1):
        value = ToolSubmit(request_id=uuid4(), telegram_id=users[user].telegram_id, kind=kind)
        path = folder(value.telegram_id, value.request_id)
        path.mkdir(parents=True)
        (path / "input").write_bytes(b"mock-input")
        return value
    try:
        yield SimpleNamespace(users=users, payload=payload)
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(MediaToolRequest).where(MediaToolRequest.user_id.in_([u.id for u in users])))
            await db.execute(delete(DownloadJob).where(DownloadJob.user_id.in_([u.id for u in users])))
            await db.execute(delete(ApplicationSetting).where(ApplicationSetting.key.like("tools.%")))
            await db.execute(delete(User).where(User.id.in_([u.id for u in users])))
            await db.commit()
        await engine.dispose()


async def accept(payload):
    async with AsyncSessionLocal() as db:
        try:
            return await submit(db, payload)
        except MediaToolError as exc:
            return exc.code


@pytest.mark.asyncio
async def test_repeated_confirmation_creates_one_paid_attempt(records):
    value = records.payload()
    results = await asyncio.gather(accept(value), accept(value))
    assert [r[1] for r in results].count(True) == 1
    assert results[0][0].id == results[1][0].id
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(MediaToolRequest)) == 1


@pytest.mark.asyncio
async def test_global_limit_is_atomic_for_two_users(records):
    async with AsyncSessionLocal() as db:
        await configure_tool(db, {"kind": "music", "enabled": True, "global_daily": 1}, records.users[0])
        await db.commit()
    results = await asyncio.gather(accept(records.payload(user=1)), accept(records.payload(user=2)))
    assert sum(isinstance(r, tuple) for r in results) == 1
    assert "tool_daily_limit" in results


@pytest.mark.asyncio
async def test_user_limit_counts_failed_attempts_and_separates_tools(records):
    for _ in range(2):
        result = await accept(records.payload())
        async with AsyncSessionLocal() as db:
            await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == result[0].id).values(status="failed"))
            await db.commit()
    assert await accept(records.payload()) == "tool_daily_limit"
    assert isinstance(await accept(records.payload("portrait")), tuple)


@pytest.mark.asyncio
async def test_disabled_and_blocked_users_do_not_create_attempts(records):
    async with AsyncSessionLocal() as db:
        await configure_tool(db, {"kind": "music", "enabled": False}, records.users[0])
        await db.execute(update(User).where(User.id == records.users[2].id).values(status=UserStatus.BLOCKED))
        await db.commit()
    assert await accept(records.payload()) == "tool_disabled"
    assert await accept(records.payload("portrait", user=2)) == "access_denied"


@pytest.mark.asyncio
async def test_owner_is_checked_and_duplicate_payload_cannot_change_kind(records):
    value = records.payload()
    await accept(value)
    assert await accept(value.model_copy(update={"kind": "portrait"})) == "request_conflict"
    async with AsyncSessionLocal() as db:
        with pytest.raises(MediaToolError, match="not_found"):
            await owned(db, value.request_id, records.users[2].telegram_id)


@pytest.mark.asyncio
async def test_worker_redelivery_does_not_repeat_provider(records, monkeypatch):
    from app.workers.tasks import media_tools as worker
    from app.services.media_tool_providers import ToolError
    value = records.payload()
    await accept(value)
    calls = []
    def recognize(*args):
        calls.append(1)
        raise ToolError("provider_uncertain")
    monkeypatch.setattr(worker, "recognize_music", recognize)
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    async with AsyncSessionLocal() as db:
        row = await db.get(MediaToolRequest, value.request_id)
        assert row.status == "uncertain" and row.error_code == "provider_uncertain"
    assert calls == [1] and not folder(value.telegram_id, value.request_id).exists()


@pytest.mark.asyncio
async def test_restore_cancels_paid_work_instead_of_replaying(records, monkeypatch):
    from app.workers.tasks import media_tools as worker
    value = records.payload()
    await accept(value)
    async with AsyncSessionLocal() as db:
        assert await reconcile_restored_tools(db) == 1
        await db.commit()
    monkeypatch.setattr(worker, "recognize_music", lambda *a: pytest.fail("restored work replayed"))
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))


@pytest.mark.asyncio
async def test_stale_work_becomes_uncertain_and_temporary_files_expire(records):
    value = records.payload("portrait")
    row, _ = await accept(value)
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    async with AsyncSessionLocal() as db:
        await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == row.id).values(
            status="processing", created_at=old, started_at=old))
        await db.commit()
        await db.run_sync(sweep_tools)
        await db.commit()
        current = await db.get(MediaToolRequest, row.id)
        assert current.status == "uncertain" and current.files_removed_at
    assert not folder(value.telegram_id, value.request_id).exists()


@pytest.mark.asyncio
async def test_api_requires_internal_key_checks_owner_and_claims_delivery_once(records):
    app = FastAPI()
    app.include_router(router)
    value = records.payload("portrait")
    await accept(value)
    path = folder(value.telegram_id, value.request_id)
    (path / "result.jpg").write_bytes(b"photo")
    async with AsyncSessionLocal() as db:
        await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == value.request_id).values(status="completed"))
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        endpoint = f"/media-tools/{value.request_id}"
        assert (await client.get(endpoint, params={"telegram_id": value.telegram_id})).status_code in (401, 403)
        headers = {"X-Internal-API-Key": settings.bot_backend_api_key}
        assert (await client.get(endpoint, params={"telegram_id": records.users[2].telegram_id}, headers=headers)).status_code == 404
        async def claim():
            return (await client.post(endpoint + "/claim-delivery", json={"telegram_id": value.telegram_id}, headers=headers)).json()
        results = await asyncio.gather(claim(), claim())
        assert [r["claimed"] for r in results].count(True) == 1
        response = await client.post(endpoint + "/release", json={"telegram_id": value.telegram_id}, headers=headers)
        assert response.status_code == 200 and not path.exists()


@pytest.mark.asyncio
async def test_tokens_encrypted_and_omitted_from_public_configuration(records):
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(ApplicationSetting).where(ApplicationSetting.key == "tools.music.token"))
        assert row.is_sensitive and row.value_json is None and "test-private-token" not in row.encrypted_value
        public = public_config(await read_config(db, "music"))
        assert public["configured"] and "token" not in public
        with pytest.raises(SettingValidationError):
            await ApplicationSettingsService(db).set_value(key="tools.music.token", category="tools", value="secret",
                is_sensitive=False, actor_user_id=records.users[0].id, actor_telegram_id=records.users[0].telegram_id)


@pytest.mark.asyncio
async def test_song_download_uses_full_source_and_repeated_click_creates_one_job(records, monkeypatch):
    from app.services.download import DownloadService
    from app.repositories.download_job import DownloadJobRepository
    value = records.payload()
    await accept(value)
    source = "https://www.youtube.com/watch?v=AbcDefGhI12"
    async with AsyncSessionLocal() as db:
        await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == value.request_id).values(
            status="completed", result={"title": "Original", "candidates": [{"url": source}]}))
        await db.commit()
    calls = []
    async def create(service, **kwargs):
        calls.append(kwargs)
        return await DownloadJobRepository(service.session).create(source_url=kwargs["source_url"],
            user_id=records.users[1].id, media_type=kwargs["media_type"], output_format=kwargs["output_format"])
    monkeypatch.setattr(DownloadService, "create_job", create)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"X-Internal-API-Key": settings.bot_backend_api_key}) as client:
        body = {"telegram_id": value.telegram_id, "candidate": 0, "output_format": "mp3"}
        first = await client.post(f"/media-tools/{value.request_id}/download", json=body)
        second = await client.post(f"/media-tools/{value.request_id}/download", json=body)
        assert first.status_code == second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
    assert calls == [{"source_url": source, "telegram_id": value.telegram_id, "media_type": "audio", "output_format": "mp3"}]


@pytest.mark.asyncio
async def test_broker_ack_loss_recovers_pending_but_never_processing_work(records, monkeypatch):
    from app.services import media_tools as service
    value = records.payload()
    await accept(value)
    calls = []
    monkeypatch.setattr(service, "publish_tool", lambda request_id: calls.append(request_id))
    await asyncio.to_thread(service.dispatch_pending_tools)
    await asyncio.to_thread(service.dispatch_pending_tools)
    assert calls == [value.request_id]
    async with AsyncSessionLocal() as db:
        await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == value.request_id).values(
            status="processing", enqueued_at=None))
        await db.commit()
    await asyncio.to_thread(service.dispatch_pending_tools)
    assert calls == [value.request_id]
