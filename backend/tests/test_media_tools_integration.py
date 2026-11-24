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
from app.models.download_job import DownloadJob, DownloadJobStatus
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
        for kind in ("music",):
            await configure_tool(db, {"kind": kind, "enabled": True, "provider": "audd", "token": "test-private-token",
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
    assert "tool_global_limit" in results


@pytest.mark.asyncio
async def test_user_limit_counts_failed_attempts_and_separates_tools(records):
    for _ in range(2):
        result = await accept(records.payload())
        async with AsyncSessionLocal() as db:
            await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == result[0].id).values(status="failed"))
            await db.commit()
    assert await accept(records.payload()) == "tool_daily_limit"
    assert isinstance(await accept(records.payload(user=2)), tuple)


@pytest.mark.asyncio
async def test_disabled_and_blocked_users_do_not_create_attempts(records):
    async with AsyncSessionLocal() as db:
        await configure_tool(db, {"kind": "music", "enabled": False}, records.users[0])
        await db.execute(update(User).where(User.id == records.users[2].id).values(status=UserStatus.BLOCKED))
        await db.commit()
    assert await accept(records.payload()) == "tool_disabled"
    assert await accept(records.payload(user=2)) == "access_denied"


@pytest.mark.asyncio
async def test_owner_is_checked_and_duplicate_payload_cannot_change_kind(records):
    value = records.payload()
    await accept(value)
    assert await accept(value.model_copy(update={"auto_mp3": True})) == "request_conflict"
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
    def recognize(*args, **kwargs):
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
    monkeypatch.setattr(worker, "recognize_music", lambda *a, **k: pytest.fail("restored work replayed"))
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))


@pytest.mark.asyncio
async def test_stale_work_becomes_uncertain_and_temporary_files_expire(records):
    value = records.payload()
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
async def test_api_requires_internal_key_checks_owner_and_cleans_input(records):
    app = FastAPI()
    app.include_router(router)
    value = records.payload()
    await accept(value)
    path = folder(value.telegram_id, value.request_id)
    async with AsyncSessionLocal() as db:
        await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == value.request_id).values(status="completed"))
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        endpoint = f"/media-tools/{value.request_id}"
        assert (await client.get(endpoint, params={"telegram_id": value.telegram_id})).status_code in (401, 403)
        headers = {"X-Internal-API-Key": settings.bot_backend_api_key}
        assert (await client.get(endpoint, params={"telegram_id": records.users[2].telegram_id}, headers=headers)).status_code == 404
        response = await client.post(endpoint + "/release", json={"telegram_id": value.telegram_id}, headers=headers)
        assert response.status_code == 200 and not path.exists()


@pytest.mark.asyncio
async def test_preview_is_owned_claimed_once_and_released_without_deleting_active_input(records):
    value = records.payload()
    await accept(value)
    path = folder(value.telegram_id, value.request_id)
    (path / "preview.mp4").write_bytes(b"preview")
    async with AsyncSessionLocal() as db:
        await db.execute(update(MediaToolRequest).where(MediaToolRequest.id == value.request_id).values(
            status="processing", result={"preview": {"width": 480, "height": 270, "duration": 12}}))
        await db.commit()
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"X-Internal-API-Key": settings.bot_backend_api_key}) as client:
        endpoint = f"/media-tools/{value.request_id}"
        assert (await client.post(endpoint + "/claim-preview", json={"telegram_id": records.users[2].telegram_id})).status_code == 404
        async def claim():
            return (await client.post(endpoint + "/claim-preview", json={"telegram_id": value.telegram_id})).json()
        claims = await asyncio.gather(claim(), claim())
        assert [r["claimed"] for r in claims].count(True) == 1
        assert (await client.post(endpoint + "/release-preview", json={"telegram_id": value.telegram_id})).json()["released"]
        assert (path / "input").exists() and not (path / "preview.mp4").exists()
        assert not (await claim())["claimed"]
    async with AsyncSessionLocal() as db:
        row = await db.get(MediaToolRequest, value.request_id)
        assert row.files_removed_at is None


@pytest.mark.asyncio
async def test_worker_cancellation_after_preview_cleans_it_and_skips_recognition(records, monkeypatch):
    from app.workers.tasks import media_tools as worker
    value = records.payload()
    await accept(value)
    path = folder(value.telegram_id, value.request_id)
    def preview(source):
        (source.parent / "preview.mp4").write_bytes(b"preview")
        with worker.WorkerSessionLocal() as db:
            db.execute(update(MediaToolRequest).where(MediaToolRequest.id == value.request_id).values(status="cancelled"))
            db.commit()
        return {"width": 480, "height": 270, "duration": 12}
    monkeypatch.setattr(worker, "prepare_preview", preview)
    monkeypatch.setattr(worker, "recognize_music", lambda *a, **k: pytest.fail("Cancelled recognition started"))
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_state", [None, "preview_sending"])
async def test_failed_recognition_preview_expires_and_input_is_removed_immediately(records, monkeypatch, delivery_state):
    from app.workers.tasks import media_tools as worker
    from app.services.media_tool_providers import ToolError
    value = records.payload()
    await accept(value)
    path = folder(value.telegram_id, value.request_id)
    def preview(source):
        (source.parent / "preview.mp4").write_bytes(b"preview")
        return {"width": 480, "height": 270, "duration": 12}
    def recognize(*args, **kwargs):
        raise ToolError("music_not_found")
    monkeypatch.setattr(worker, "prepare_preview", preview)
    monkeypatch.setattr(worker, "recognize_music", recognize)
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    assert not (path / "input").exists() and (path / "preview.mp4").exists()
    async with AsyncSessionLocal() as db:
        row = await db.get(MediaToolRequest, value.request_id)
        row.delivery_state = delivery_state
        await db.commit()
        assert row.status == "failed" and row.result["preview"] and row.files_removed_at is None
        await db.run_sync(sweep_tools)
        assert (path / "preview.mp4").exists()
        await db.run_sync(lambda session: sweep_tools(session, now=datetime.now(timezone.utc) + timedelta(minutes=11)))
        await db.commit()
        assert not path.exists() and row.files_removed_at


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
async def test_shazam_accepts_and_processes_without_audd_key(records, monkeypatch):
    from app.workers.tasks import media_tools as worker
    async with AsyncSessionLocal() as db:
        await db.execute(delete(ApplicationSetting).where(ApplicationSetting.key == "tools.music.token"))
        config = await configure_tool(db, {"kind": "music", "enabled": True, "provider": "shazamio"}, records.users[0])
        await db.commit()
        assert config["available"] and not config["token_configured"]
    value = records.payload()
    await accept(value)
    calls = []
    def recognize(source, token, *, provider, check_cancelled):
        check_cancelled()
        assert provider == "shazamio" and token == ""
        calls.append(1)
        return {"title": "Song", "artist": "Artist", "duration": None}
    monkeypatch.setattr(worker, "recognize_music", recognize)
    monkeypatch.setattr(worker, "find_full_tracks", lambda track: [])
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    async with AsyncSessionLocal() as db:
        row = await db.get(MediaToolRequest, value.request_id)
        assert row.status == "completed" and row.files_removed_at is not None
        with pytest.raises(ValueError, match="provider key"):
            await configure_tool(db, {"kind": "music", "enabled": True, "provider": "audd"}, records.users[0])
    assert calls == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["https://www.youtube.com/watch?v=AbcDefGhI12",
    "https://dls.musics-fa.com/test/Example%20Artist%20-%20Original%20Song%20(320).mp3"])
async def test_song_download_uses_full_source_and_repeated_click_creates_one_job(records, monkeypatch, source):
    from app.services.download import DownloadService
    from app.repositories.download_job import DownloadJobRepository
    value = records.payload()
    await accept(value)
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
        automatic = await client.post(f"/media-tools/{value.request_id}/download", json={**body, "automatic": True})
        assert automatic.status_code == 409 and automatic.json()["detail"]["code"] == "music_selection_required"
        first = await client.post(f"/media-tools/{value.request_id}/download", json=body)
        second = await client.post(f"/media-tools/{value.request_id}/download", json=body)
        assert first.status_code == second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        another = await client.post(f"/media-tools/{value.request_id}/download", json={**body, "output_format": "flac"})
        assert another.status_code == 200 and another.json()["output_format"] == "flac"
        assert another.json()["id"] != first.json()["id"]
        async with AsyncSessionLocal() as db:
            await db.execute(update(DownloadJob).where(DownloadJob.id.in_([first.json()["id"], another.json()["id"]])).values(
                status=DownloadJobStatus.COMPLETED, file_path="/app/downloads/test-audio.mp3", file_size=100))
            await db.commit()
        endpoint = f"/media-tools/{value.request_id}/claim-song-delivery"
        async def claim():
            return (await client.post(endpoint, json=body)).json()
        claims = await asyncio.gather(claim(), claim())
        assert [r["claimed"] for r in claims].count(True) == 1
        assert (await client.post(endpoint, json=body)).json()["reason"] == "delivery_started"
        assert (await client.post(endpoint, json={**body, "output_format": "flac"})).json()["claimed"]
        assert (await client.post(endpoint, json={**body, "telegram_id": records.users[2].telegram_id})).status_code == 404
    assert calls == [{"source_url": source, "telegram_id": value.telegram_id, "media_type": "audio", "output_format": fmt}
                     for fmt in ("mp3", "flac")]


@pytest.mark.asyncio
async def test_link_admission_needs_no_upload_and_rejects_unsafe_urls_before_provider(records):
    value = records.payload().model_copy(update={"source_url": "https://www.instagram.com/reel/Example/", "auto_mp3": True})
    (folder(value.telegram_id, value.request_id) / "input").unlink()
    row, created = await accept(value)
    assert created and row.options == {"source_url": value.source_url, "auto_mp3": True}
    assert (await accept(value))[1] is False
    bad = value.model_copy(update={"request_id": uuid4(), "source_url": "http://127.0.0.1/private"})
    assert await accept(bad) == "invalid_music_link"


@pytest.mark.asyncio
async def test_stored_music_diagnostic_reads_actual_failure_without_network(records, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    import sys
    from app.services import youtube_health
    value = records.payload()
    await accept(value)
    async with AsyncSessionLocal() as db:
        failed = DownloadJob(user_id=records.users[1].id, source_url="https://youtu.be/AbcDefGhI12",
            media_type="audio", output_format="mp3", status=DownloadJobStatus.FAILED,
            error_message="FFmpeg media conversion failed: Unknown encoder 'libmp3lame' token=private")
        unrelated = DownloadJob(user_id=records.users[1].id, source_url="https://youtu.be/ABCDEFGHIJK",
            media_type="audio", output_format="mp3", status=DownloadJobStatus.FAILED)
        db.add_all([failed, unrelated])
        await db.flush()
        # The diagnostic must also find a download stored in the per-format map.
        row = await db.get(MediaToolRequest, value.request_id)
        row.downloads = {"0:mp3": {"job_id": failed.id, "state": "created"}}
        await db.commit()
        failed_id, unrelated_id = failed.id, unrelated.id
    script = Path(__file__).resolve().parents[2] / "scripts/diagnose_music_download.py"
    spec = importlib.util.spec_from_file_location("music_diagnostic_integration", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["diagnostic", "--latest", "--stored-only"])
    monkeypatch.setattr(youtube_health, "provider_health", lambda: pytest.fail("network call in stored-only mode"))
    await asyncio.to_thread(module.main)
    import json
    reports = [json.loads(line.removeprefix("RECORDED=")) for line in capsys.readouterr().out.splitlines()
               if line.startswith("RECORDED=")]
    report = next(item for item in reports if item["id"] == failed_id)
    assert report["reason"] == "ENCODER_MISSING" and report["stage"] == "conversion"
    assert unrelated_id not in [item["id"] for item in reports]
    assert "private" not in str(reports) and "youtu.be" not in str(reports)


@pytest.mark.asyncio
async def test_retry_failed_song_uses_normal_admission_and_old_click_cannot_duplicate_it(records, monkeypatch):
    from app.services.download import DownloadService
    from app.repositories.download_job import DownloadJobRepository
    value = records.payload()
    await accept(value)
    async with AsyncSessionLocal() as db:
        old = DownloadJob(user_id=records.users[1].id, source_url="https://youtu.be/AbcDefGhI12",
                          media_type="audio", output_format="mp3", status=DownloadJobStatus.FAILED)
        db.add(old)
        await db.flush()
        row = await db.get(MediaToolRequest, value.request_id)
        row.status, row.download_job_id = "completed", old.id
        row.result = {"candidates": [{"url": old.source_url}]}
        row.downloads = {"0:mp3": {"state": "created", "job_id": old.id}}
        await db.commit()
    calls = []
    async def create(service, **kwargs):
        calls.append(kwargs)
        return await DownloadJobRepository(service.session).create(user_id=records.users[1].id,
            source_url=kwargs["source_url"], media_type="audio", output_format="mp3")
    monkeypatch.setattr(DownloadService, "create_job", create)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"X-Internal-API-Key": settings.bot_backend_api_key}) as client:
        endpoint = f"/media-tools/{value.request_id}/download"
        body = {"telegram_id": value.telegram_id, "candidate": 0, "output_format": "mp3"}
        assert (await client.post(endpoint, json=body)).json()["id"] == old.id
        retry = {**body, "retry_failed_job_id": old.id}
        results = await asyncio.gather(client.post(endpoint, json=retry), client.post(endpoint, json=retry))
        assert len(calls) == 1 and all(r.status_code in (200, 409) for r in results)
        current = (await client.post(endpoint, json=retry)).json()
        assert current["id"] != old.id and len(calls) == 1
        # An in-flight or already delivered job is never retried by this API.
        refused = await client.post(endpoint, json={**body, "retry_failed_job_id": current["id"]})
        assert refused.status_code == 409 and len(calls) == 1
        assert (await client.post(endpoint, json={**retry, "telegram_id": records.users[2].telegram_id})).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_during_fetch", [False, True])
@pytest.mark.parametrize("reference_duration", [None, 180])
@pytest.mark.parametrize("recognition_provider", ["audd", "shazamio"])
async def test_link_sample_recognizes_once_and_cancellation_stops_before_billing(records, monkeypatch, cancel_during_fetch, reference_duration, recognition_provider):
    from app.workers.tasks import media_tools as worker
    from app.services import media_tool_links
    async with AsyncSessionLocal() as db:
        await configure_tool(db, {"kind": "music", "enabled": True, "provider": recognition_provider}, records.users[0])
        await db.commit()
    value = records.payload().model_copy(update={"source_url": "https://youtu.be/AbcDefGhI12", "auto_mp3": True})
    (folder(value.telegram_id, value.request_id) / "input").unlink()
    await accept(value)
    calls = []
    def fetch(url, target, *, seconds):
        assert url == value.source_url
        assert seconds == (36 if recognition_provider == "shazamio" else 12)
        target.write_bytes(b"sample-from-public-link")
        if cancel_during_fetch:
            with worker.WorkerSessionLocal() as db:
                db.execute(update(MediaToolRequest).where(MediaToolRequest.id == value.request_id).values(status="cancelled"))
                db.commit()
        return target
    def recognize(source, token, *, provider, check_cancelled):
        check_cancelled()
        assert source.read_bytes() == b"sample-from-public-link"
        assert provider == recognition_provider
        calls.append(1)
        return {"title": "Original Song", "artist": "Example Artist", "duration": reference_duration}
    monkeypatch.setattr(media_tool_links, "fetch_music_link", fetch)
    monkeypatch.setattr(worker, "recognize_music", recognize)
    monkeypatch.setattr(worker, "find_full_tracks", lambda track: [{"title": "Example Artist Original Song", "channel": "Example Artist",
        "duration": 180, "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"}])
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    async with AsyncSessionLocal() as db:
        row = await db.get(MediaToolRequest, value.request_id)
        assert row.status == ("cancelled" if cancel_during_fetch else "completed")
        if not cancel_during_fetch:
            assert row.result["automatic_candidate"] == 0
            assert row.result["candidates"][0]["url"] != value.source_url
    assert calls == ([] if cancel_during_fetch else [1])
    assert not folder(value.telegram_id, value.request_id).exists()


@pytest.mark.asyncio
async def test_failed_link_never_calls_paid_recognition_and_cleans_partial_files(records, monkeypatch):
    from app.workers.tasks import media_tools as worker
    from app.services import media_tool_links
    from app.services.media_tool_providers import ToolError
    value = records.payload().model_copy(update={"source_url": "https://youtu.be/AbcDefGhI12"})
    (folder(value.telegram_id, value.request_id) / "input").unlink()
    await accept(value)
    def fail(url, target, **kwargs):
        (target.parent / "link-source.webm.part").write_bytes(b"partial")
        raise ToolError("link_timeout")
    monkeypatch.setattr(media_tool_links, "fetch_music_link", fail)
    monkeypatch.setattr(worker, "recognize_music", lambda *a, **k: pytest.fail("billed recognition after failed link"))
    await asyncio.to_thread(worker.run_tool.run, str(value.request_id))
    async with AsyncSessionLocal() as db:
        row = await db.get(MediaToolRequest, value.request_id)
        assert row.status == "failed" and row.error_code == "link_timeout"
    assert not folder(value.telegram_id, value.request_id).exists()


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


@pytest.mark.asyncio
async def test_removed_portrait_api_and_legacy_queue_never_call_provider(records, monkeypatch):
    from app.workers.tasks import media_tools as worker
    from app.db.worker_session import WorkerSessionLocal
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ToolSubmit(telegram_id=records.users[1].telegram_id,request_id=uuid4(),kind='portrait')
    value=records.payload()
    await accept(value)
    async with AsyncSessionLocal() as db:
        row=await db.get(MediaToolRequest,value.request_id);row.kind='portrait';await db.commit()
        with pytest.raises(ValueError):
            await configure_tool(db,{'kind':'portrait','enabled':True,'token':'unused'},records.users[0])
    monkeypatch.setattr(worker,'recognize_music',lambda *a,**k:pytest.fail('Retired request contacted a provider'))
    await asyncio.to_thread(worker.run_tool.run,str(value.request_id))
    async with AsyncSessionLocal() as db:
        row=await db.get(MediaToolRequest,value.request_id)
        assert row.status=='cancelled' and row.error_code=='tool_disabled'
    assert not folder(value.telegram_id,value.request_id).exists()


@pytest.mark.asyncio
async def test_music_plan_weekly_limit_and_live_plan_edits(records):
    from app.models.plan import Plan
    from app.models.subscription import Subscription
    from app.services.download_access import quota_window_start_utc
    user=records.users[1]
    async with AsyncSessionLocal() as db:
        plan=Plan(name='Music test',slug='music_'+uuid4().hex,price=100,duration_days=30,
                  max_file_size_mb=1900,max_concurrent_downloads=1,is_active=True,is_system=False,
                  tool_limits={'music':{'limit':1,'period':'weekly'}})
        db.add(plan);await db.flush()
        sub=Subscription(user_id=user.id,plan_id=plan.id,started_at=datetime.now(timezone.utc)-timedelta(days=1),
                         expires_at=datetime.now(timezone.utc)+timedelta(days=29),daily_download_limit=10)
        db.add(sub);await db.commit();plan_id=plan.id
    try:
        first=await accept(records.payload());assert isinstance(first,tuple)
        async with AsyncSessionLocal() as db:
            row=await db.get(MediaToolRequest,first[0].id);row.status='failed';await db.commit()
        assert await accept(records.payload())=='tool_weekly_limit'
        async with AsyncSessionLocal() as db:
            row=await db.get(MediaToolRequest,first[0].id)
            row.created_at=quota_window_start_utc('weekly',timezone_name='Asia/Tehran')-timedelta(seconds=1)
            await db.commit()
        second=await accept(records.payload());assert isinstance(second,tuple)
        async with AsyncSessionLocal() as db:
            row=await db.get(MediaToolRequest,second[0].id);row.status='failed'
            plan=await db.get(Plan,plan_id);plan.tool_limits={'music':{'limit':-1,'period':'weekly'}};await db.commit()
        assert isinstance(await accept(records.payload()),tuple)
        async with AsyncSessionLocal() as db:
            await db.execute(update(MediaToolRequest).where(MediaToolRequest.user_id==user.id).values(status='failed'))
            plan=await db.get(Plan,plan_id);plan.tool_limits={'music':{'limit':0,'period':'daily'}};await db.commit()
        assert await accept(records.payload())=='tool_plan_disabled'
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Subscription).where(Subscription.plan_id==plan_id))
            await db.execute(delete(Plan).where(Plan.id==plan_id));await db.commit()
