"""Synthetic load/ownership tests; no real source requests or Telegram sends."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, func

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.admin import AdminAccount
from app.models.download_job import DownloadJob, DownloadJobStatus as Status
from app.models.public_output import PublicOutput
from app.models.user import User, UserStatus
from app.services import download_capacity as capacity
from app.services.download import DownloadService
from app.services.download_access import DownloadAccessService, DownloadEntitlement, DailyDownloadLimitReached


def output(**changes):
    return SimpleNamespace(**{**dict(source_url="https://www.instagram.com/reel/PublicTest/",
        media_type="video", format_id="avc", quality="720p", output_format=None,
        playlist_index=None, delivery_kind="document"), **changes})


@pytest.mark.parametrize("url", ["upload://upload-private.mp4", "https://example.com/private.mp4",
    "https://instagram.com/stories/example/123/", "https://instagram.com/direct/inbox/",
    "https://www.youtube.com/watch?v=AAAAAAAAAAA", "http://127.0.0.1/file.mp3",
    "https://user:password@instagram.com/reel/PublicTest/", "https://instagram.com.evil.invalid/reel/PublicTest/"])
def test_private_identity_never_becomes_public_cache(url):
    assert capacity.output_key(output(source_url=url)) is None


def test_identity_preserves_bot_item_container_quality_and_telegram_kind(monkeypatch):
    base = capacity.output_key(output())
    assert base == capacity.output_key(output(source_url="https://instagram.com/p/PublicTest/?igsh=tracking"))
    for change in (dict(playlist_index=2), dict(output_format="mp4"), dict(quality="1080p"),
                   dict(delivery_kind="audio"), dict(format_id="vp9"), dict(media_type="audio"),
                   dict(source_url="https://instagram.com/p/PublicTest/?img_index=2")):
        assert base != capacity.output_key(output(**change))
    monkeypatch.setattr(settings, "telegram_bot_token", "987654321:other-test-bot")
    assert base != capacity.output_key(output())


def test_launcher_reserves_a_separate_pool_for_short_tasks():
    from app.workers.launcher import commands
    download, tools = commands(2)
    assert "--queues=downloads" in download and "--concurrency=2" in download
    assert "--queues=celery" in tools and "--concurrency=1" in tools
    assert "--prefetch-multiplier=1" in download and "--prefetch-multiplier=1" in tools


@pytest_asyncio.fixture
async def traffic(monkeypatch):
    assert settings.app_env == "test", "Disposable PostgreSQL database required"
    from app.services import download
    monkeypatch.setattr(download, "check_instagram_access", lambda _: None)
    queued = Mock(side_effect=lambda **_: SimpleNamespace(id=uuid4().hex))
    monkeypatch.setattr(download.download_task, "apply_async", queued)
    async with AsyncSessionLocal() as db:
        users = [User(telegram_id=8_650_000_000_000 + uuid4().int % 100_000_000,
                      first_name="Synthetic traffic", status=UserStatus.ACTIVE) for _ in range(40)]
        db.add_all(users)
        await db.flush()
        for user in users:
            db.add(AdminAccount(user_id=user.id, is_superadmin=True, is_active=True, created_by_user_id=user.id))
        await db.commit()
    record = SimpleNamespace(users=users, queued=queued,
        url="https://www.instagram.com/reel/Test" + uuid4().hex + "/")
    try:
        yield record
    finally:
        async with AsyncSessionLocal() as db:
            ids = [user.id for user in users]
            jobs = select(DownloadJob.id).where(DownloadJob.user_id.in_(ids))
            await db.execute(delete(PublicOutput).where(PublicOutput.leader_job_id.in_(jobs)))
            await db.execute(delete(DownloadJob).where(DownloadJob.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.commit()
        await engine.dispose()


async def request(record, index, **changes):
    async with AsyncSessionLocal() as db:
        return await DownloadService(db).create_job(**{**dict(source_url=record.url,
            telegram_id=record.users[index].telegram_id, quality="720p", media_type="video"), **changes})


async def publish(record, job_id):
    async with AsyncSessionLocal() as db:
        job = await db.get(DownloadJob, job_id)
        job.status, job.file_size = Status.COMPLETED, 1024
        job.completed_at, job.file_path = datetime.now(timezone.utc), "/not-shared/test.mp4"
        await db.commit()
        assert (await capacity.claim_delivery(db, job_id))["claimed"]
        await capacity.confirm_delivery(db, job_id, SimpleNamespace(
            file_id="telegram-public-file", file_kind="document", filename="Public title.mp4"))
        # The sender already deleted the local media bytes after Telegram ACK.
        job.files_removed_at = datetime.now(timezone.utc)
        await db.commit()


@pytest.mark.asyncio
async def test_concurrent_public_requests_download_once_then_use_telegram_and_keep_quota(traffic):
    jobs = await asyncio.gather(*(request(traffic, i) for i in range(40)))
    assert traffic.queued.call_count == 1
    leader_id = traffic.queued.call_args.kwargs["args"][0]
    assert len({job.user_id for job in jobs}) == 40
    assert all(job.status == Status.PENDING and job.telegram_file_id is None for job in jobs)
    await publish(traffic, leader_id)

    async def resolve(job):
        async with AsyncSessionLocal() as db:
            return await DownloadService(db).get_job(job.id)
    followers = await asyncio.gather(*(resolve(job) for job in jobs if job.id != leader_id))
    assert len(followers) == 39 and traffic.queued.call_count == 1
    assert all(job.status == Status.COMPLETED and job.file_path is None
               and job.telegram_file_id == "telegram-public-file" for job in followers)
    job = followers[0]
    async with AsyncSessionLocal() as db:
        entitlement = DownloadEntitlement(user_id=job.user_id, plan_id=None, plan_name="Test", plan_slug="paid",
            daily_download_limit=1, max_file_size_mb=10, max_quality=1080, max_concurrent_downloads=1,
            priority_processing=False, forced_join_required=False)
        with pytest.raises(DailyDownloadLimitReached):
            await DownloadAccessService(db)._validate_download_limit(entitlement)
    async def claim():
        async with AsyncSessionLocal() as db:
            return await capacity.claim_delivery(db, job.id)
    claims = await asyncio.gather(claim(), claim())
    assert sum(item["claimed"] for item in claims) == 1
    # A restarted poller cannot resend a delivery with an unknown Telegram ACK.
    assert not (await claim())["claimed"]


@pytest.mark.asyncio
async def test_cancelled_leader_promotes_one_follower_without_reusing_local_bytes(traffic):
    jobs = await asyncio.gather(*(request(traffic, i) for i in range(12)))
    leader_id = traffic.queued.call_args.kwargs["args"][0]
    async with AsyncSessionLocal() as db:
        job = await db.get(DownloadJob, leader_id)
        job.status, job.file_path = Status.CANCELLED, "/never-share/partial.mp4"
        await db.commit()
    async def poll(job):
        async with AsyncSessionLocal() as db:
            return await DownloadService(db).get_job(job.id)
    result = await asyncio.gather(*(poll(job) for job in jobs if job.id != leader_id))
    assert traffic.queued.call_count == 2
    assert all(job.file_path is None and job.status == Status.PENDING for job in result)


@pytest.mark.asyncio
async def test_unique_load_is_bounded_and_rejected_admissions_do_not_reserve_quota(traffic, monkeypatch):
    async with AsyncSessionLocal() as db:
        baseline = await db.scalar(select(func.count(DownloadJob.id)).where(
            DownloadJob.status.in_(capacity.ACTIVE)))
        # Include completed outputs awaiting delivery just like admission does.
        baseline += await db.scalar(select(func.count(DownloadJob.id)).where(DownloadJob.status == Status.COMPLETED,
            DownloadJob.delivered_at.is_(None), DownloadJob.files_removed_at.is_(None)))
        from app.models.media_tool import MediaToolRequest
        baseline += await db.scalar(select(func.count(MediaToolRequest.id)).where(MediaToolRequest.status.in_(("pending", "processing"))))
    original = capacity.get_managed_setting
    async def bounded(db, key):
        return int(baseline) + 5 if key == "downloads.queue_limit" else await original(db, key)
    monkeypatch.setattr(capacity, "get_managed_setting", bounded)
    results = await asyncio.gather(*(request(traffic, i, source_url=traffic.url.replace("/Test", f"/Test{i}"))
                                    for i in range(40)), return_exceptions=True)
    assert sum(isinstance(item, DownloadJob) for item in results) == 5
    assert sum(isinstance(item, capacity.DownloadQueueFull) for item in results) == 35
    assert traffic.queued.call_count == 5
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count(DownloadJob.id)).where(
            DownloadJob.user_id.in_([u.id for u in traffic.users]))) == 5


@pytest.mark.asyncio
async def test_private_uploads_never_join_or_publish_shared_output(traffic):
    jobs = await asyncio.gather(*(request(traffic, i, source_url="upload://upload-private.mp4",
        media_type="convert", output_format="mp3", quality=None) for i in range(8)))
    assert traffic.queued.call_count == 8
    assert all(job.public_output_key is None and job.telegram_file_id is None for job in jobs)


@pytest.mark.asyncio
async def test_broker_interruption_is_recoverable_and_restore_cannot_replay_cache(traffic):
    traffic.queued.side_effect = OSError("test broker outage")
    job = await request(traffic, 0)
    assert job.status == Status.PENDING and job.enqueued_at is None
    traffic.queued.side_effect = lambda **_: SimpleNamespace(id=uuid4().hex)
    await capacity.recover_pending_downloads()
    async with AsyncSessionLocal() as db:
        fresh = await db.get(DownloadJob, job.id)
        assert fresh.enqueued_at and fresh.celery_task_id
    await publish(traffic, job.id)
    async with AsyncSessionLocal() as db:
        await capacity.clear_restored_outputs(db)
        await db.commit()
        assert await db.scalar(select(func.count(PublicOutput.key))) == 0
    fresh = await request(traffic, 1)
    assert fresh.status == Status.PENDING and fresh.telegram_file_id is None


@pytest.mark.asyncio
async def test_expiry_and_definite_telegram_rejection_fetch_a_fresh_source(traffic):
    job = await request(traffic, 0)
    await publish(traffic, job.id)
    hit = await request(traffic, 1)
    assert hit.telegram_file_id
    async with AsyncSessionLocal() as db:
        await capacity.invalidate_cached_delivery(db, hit.id)
    retry = await request(traffic, 1)
    assert retry.status == Status.PENDING and retry.telegram_file_id is None
    await publish(traffic, retry.id)
    async with AsyncSessionLocal() as db:
        output = await db.get(PublicOutput, retry.public_output_key)
        output.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    expired = await request(traffic, 2)
    assert expired.status == Status.PENDING and expired.telegram_file_id is None


@pytest.mark.asyncio
async def test_cached_music_link_never_downloads_another_clip_or_calls_recognizer(traffic, monkeypatch, tmp_path):
    from app.models.media_tool import MediaToolRequest
    from app.services import media_tool_links, music_link_cache, media_tool_files
    from app.workers.tasks import media_tools
    request_id = uuid4()
    async with AsyncSessionLocal() as db:
        db.add(MediaToolRequest(id=request_id, user_id=traffic.users[0].id, kind="music", status="pending",
                               options={"source_url": traffic.url, "auto_mp3": True}))
        await db.commit()
    monkeypatch.setattr(media_tools, "read_worker_config", lambda *_:
        {"enabled": True, "provider": "shazamio", "token": ""})
    monkeypatch.setattr(media_tool_files, "root", lambda: tmp_path)
    monkeypatch.setattr(music_link_cache, "key", lambda *_: "a" * 64)
    monkeypatch.setattr(music_link_cache, "read", lambda _: ({"title": "Public song", "artist": "Example artist",
        "candidates": [{"url": "https://example.invalid/full-song"}], "automatic_candidate": 0},
        {"telegram_file_id": "preview-reference", "width": 320, "height": 240, "duration": 12}))
    monkeypatch.setattr(media_tool_links, "fetch_music_link", lambda *_a, **_kw: pytest.fail("Repeated source fetch"))
    monkeypatch.setattr(media_tools, "recognize_music", lambda *_a, **_kw: pytest.fail("Repeated recognition"))
    media_tools.run_tool.run(str(request_id))
    async with AsyncSessionLocal() as db:
        row = await db.get(MediaToolRequest, request_id)
        assert row.user_id == traffic.users[0].id and row.status == "completed"
        assert row.result["preview"]["telegram_file_id"] == "preview-reference"
        assert row.files_removed_at is not None
    assert not list(tmp_path.rglob("*"))


@pytest.mark.asyncio
async def test_blocked_user_cannot_use_a_warm_file_reference(traffic):
    from app.services.download_access import DownloadUserBlocked
    job = await request(traffic, 0)
    await publish(traffic, job.id)
    async with AsyncSessionLocal() as db:
        user = await db.get(User, traffic.users[1].id)
        user.status = UserStatus.BLOCKED
        await db.commit()
    with pytest.raises(DownloadUserBlocked):
        await request(traffic, 1)


@pytest.mark.asyncio
async def test_paused_follower_resumes_without_pausing_or_duplicating_leader(traffic, monkeypatch):
    from app.services import download
    monkeypatch.setattr(download.cleanup_paused_download, "apply_async", Mock())
    leader, follower = await request(traffic, 0), await request(traffic, 1)
    async with AsyncSessionLocal() as db:
        paused = await DownloadService(db).pause_job(follower.id)
        assert paused.status == Status.PAUSED
    await publish(traffic, leader.id)
    async with AsyncSessionLocal() as db:
        paused = await DownloadService(db).get_job(follower.id)
        assert paused.status == Status.PAUSED and paused.telegram_file_id is None
        resumed = await DownloadService(db).resume_job(follower.id)
        assert resumed.status == Status.COMPLETED and resumed.telegram_file_id
    assert traffic.queued.call_count == 1


@pytest.mark.asyncio
async def test_duplicate_worker_delivery_cannot_start_source_work_twice(traffic):
    from app.workers.tasks.download import _set_processing
    job = await request(traffic, 0)
    claims = await asyncio.gather(*(asyncio.to_thread(_set_processing, job.id, task_id=f"delivery-{n}") for n in range(8)))
    assert sum(claims) == 1
    async with AsyncSessionLocal() as db:
        current = await db.get(DownloadJob, job.id)
        owner = current.execution_token
    assert not _set_processing(job.id, allow_retry=True, task_id="different-delivery")
    assert _set_processing(job.id, allow_retry=True, task_id=owner)
