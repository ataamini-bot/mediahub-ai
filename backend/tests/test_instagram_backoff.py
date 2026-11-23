import math
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.error import HTTPError

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from app.services import instagram_access as guard


URL = "https://www.instagram.com/reel/PublicTest/"


@pytest.fixture
def cooldown(monkeypatch):
    clock = SimpleNamespace(now=1000.0, until=0.0, writes=0)
    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(guard, "_local_until", 0.0)
    monkeypatch.setattr(guard, "_redis_retry_at", 0.0)

    def storage(seconds=None):
        if seconds is not None:
            clock.until = max(clock.until, clock.now + seconds)
            clock.writes += 1
        return max(0, math.ceil(clock.until - clock.now))

    monkeypatch.setattr(guard, "_redis_operation", storage)
    return clock


def throttled(header=None):
    return HTTPError(URL, 429, "Too Many Requests", {"Retry-After": header} if header else {}, None)


def test_first_429_stops_extractor_and_metadata_fallback(monkeypatch, cooldown):
    from app.services.download import DownloadService
    calls = []

    def fail(_self, request):
        calls.append(request)
        raise throttled("120")

    monkeypatch.setattr(YoutubeDL, "urlopen", fail)
    with pytest.raises(guard.InstagramRateLimited) as caught:
        DownloadService.get_media_info(URL)
    assert caught.value.retry_after == 120
    assert len(calls) == 1
    assert cooldown.writes == 1
    # A different post is blocked too, without another extraction or request.
    with pytest.raises(guard.InstagramRateLimited):
        DownloadService.get_media_info("https://www.instagram.com/p/OtherPost/")
    assert len(calls) == 1 and cooldown.writes == 1


@pytest.mark.parametrize("header,seconds", [(None, 900), ("bad", 900), ("-5", 900), ("0", 900), ("60", 60), ("3600", 3600)])
def test_wrapped_http_error_retains_retry_after(cooldown, header, seconds):
    original = throttled(header)
    wrapped = ExtractorError("Unable to download webpage: HTTP Error 429", cause=original)
    error = DownloadError("ERROR: HTTP Error 429", exc_info=(type(wrapped), wrapped, None))
    limited = guard.rate_limit_error(error, URL)
    assert limited.retry_after == seconds
    assert limited.detail() == {"code": "instagram_rate_limited", "retry_after": seconds}
    assert str(limited) == "instagram_rate_limited" and URL not in str(limited)


def test_http_date_header_and_shared_expiry(cooldown, monkeypatch):
    header = format_datetime(datetime.now(timezone.utc) + timedelta(minutes=30), usegmt=True)
    guard.rate_limit_error(throttled(header), URL)
    assert 1798 <= guard.remaining_seconds() <= 1800
    # A new process gets the shared deadline; reads never refresh its expiry.
    monkeypatch.setattr(guard, "_local_until", 0.0)
    cooldown.now += 100
    assert 1698 <= guard.remaining_seconds() <= 1700
    assert cooldown.writes == 1
    cooldown.now += 1800
    guard.check_instagram_access(URL)
    assert guard.remaining_seconds() == 0


def test_shorter_response_does_not_shorten_existing_pause(cooldown):
    guard.rate_limit_error(throttled("3600"), URL)
    cooldown.now += 100
    guard.rate_limit_error(throttled("60"), URL)
    assert guard.remaining_seconds() == 3500
    writes = cooldown.writes
    try:
        guard.check_instagram_access(URL)
    except guard.InstagramRateLimited as exc:
        assert guard.rate_limit_error(exc, URL) is exc
    assert cooldown.writes == writes


@pytest.mark.parametrize("url", ["https://youtube.com/watch?v=example", "upload://input.mp3", "https://instagram.com.example.test/p/a"])
def test_other_sources_keep_their_options_and_networking(monkeypatch, cooldown, url):
    guard.rate_limit_error(throttled(), URL)
    options = {"quiet": True, "retries": 3, "extractor_args": {"youtube": {"player_client": ["mweb"]}}}
    with guard.media_downloader(url, options) as downloader:
        assert downloader.params["retries"] == 3
        assert downloader.params["extractor_args"] == options["extractor_args"]
        assert "urlopen" not in downloader.__dict__
    assert guard.rate_limit_error(throttled(), url) is None


def test_non_429_and_no_fake_rate_limit_from_video_ids(cooldown):
    for error in (HTTPError(URL, 404, "Not found", {}, None), ValueError("video id 429 missing")):
        assert guard.rate_limit_error(error, URL) is None
    assert cooldown.writes == 0


def test_instagram_keeps_other_retry_settings(cooldown):
    with guard.media_downloader(URL, {"quiet": True, "retries": 4, "extractor_retries": 2}) as downloader:
        assert downloader.params["retries"] == 4
        assert downloader.params["extractor_retries"] == 2
        assert "urlopen" in downloader.__dict__


def test_redis_outage_keeps_observed_local_pause(monkeypatch):
    monkeypatch.setattr(guard, "_local_until", 0.0)
    monkeypatch.setattr(guard, "_redis_retry_at", 0.0)
    factory = MagicMock(side_effect=guard.redis.ConnectionError("private connection detail"))
    monkeypatch.setattr(guard.redis.Redis, "from_url", factory)
    guard.rate_limit_error(throttled("90"), URL)
    with pytest.raises(guard.InstagramRateLimited):
        guard.check_instagram_access(URL)
    assert factory.call_count == 1  # A brief outage doesn't repeatedly wait on Redis.


@pytest.mark.asyncio
async def test_known_cooldown_rejects_download_before_admission(cooldown):
    from app.services.download import DownloadService
    guard.rate_limit_error(throttled(), URL)
    with pytest.raises(guard.InstagramRateLimited):
        await DownloadService(None).create_job(URL, telegram_id=123, quality="720p")


@pytest.mark.asyncio
async def test_metadata_api_returns_structured_retry_information(monkeypatch, cooldown):
    from app.api import downloads
    from fastapi import HTTPException
    monkeypatch.setattr(downloads, "ensure_public_operation", AsyncMock())
    guard.rate_limit_error(throttled("120"), URL)
    with pytest.raises(HTTPException) as caught:
        await downloads.get_media_info(url=URL, playlist_index=None, db=None)
    assert caught.value.status_code == 503
    assert caught.value.detail == {"code": "instagram_rate_limited", "retry_after": 120}
    assert caught.value.headers == {"Retry-After": "120"}


@pytest.mark.asyncio
async def test_music_admission_does_not_create_or_charge_a_known_blocked_link(monkeypatch, cooldown):
    from app.services import media_tools
    from app.models.user import UserStatus
    guard.rate_limit_error(throttled(), URL)
    db = MagicMock()
    db.execute = AsyncMock()
    db.scalar = AsyncMock(return_value=SimpleNamespace(id=1, status=UserStatus.ACTIVE))
    db.get = AsyncMock(return_value=None)
    db.commit = AsyncMock()
    monkeypatch.setattr(media_tools, "ensure_public_operation", AsyncMock())
    monkeypatch.setattr(media_tools, "read_config", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(media_tools, "provider_configured", lambda _config: True)
    data = SimpleNamespace(telegram_id=123, kind="music", auto_mp3=True, request_id="synthetic", source_url=URL)
    with pytest.raises(media_tools.MediaToolError, match="instagram_rate_limited"):
        await media_tools.submit(db, data)
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


def test_music_sample_does_not_spawn_during_cooldown(monkeypatch, cooldown, tmp_path):
    from app.services import media_tool_links as links
    from app.services.media_tool_providers import ToolError
    guard.rate_limit_error(throttled(), URL)
    process = MagicMock(side_effect=AssertionError("No child may start during backoff"))
    monkeypatch.setattr(links.subprocess, "Popen", process)
    with pytest.raises(ToolError, match="instagram_rate_limited"):
        links.fetch_music_link(URL, tmp_path / "input")
    process.assert_not_called()


def test_sample_child_rate_limit_keeps_its_reason_and_cleans_files(monkeypatch, cooldown, tmp_path):
    from app.services import media_tool_links as links
    from app.services.media_tool_providers import ToolError

    class Process:
        def wait(self, **_kwargs):
            (tmp_path / "link-source.mp4.part").write_bytes(b"synthetic partial")
            (tmp_path / "input").write_bytes(b"synthetic input")
            return 75

    monkeypatch.setattr(links.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(ToolError, match="instagram_rate_limited"):
        links.fetch_music_link(URL, tmp_path / "input")
    assert list(tmp_path.iterdir()) == []


def test_worker_rate_limit_cleans_files_and_skips_celery_retry(monkeypatch, cooldown, tmp_path):
    from app.workers.tasks import download as worker
    from app.models.download_job import DownloadJobStatus
    job = SimpleNamespace(status=DownloadJobStatus.PENDING, source_url=URL, format_id=None,
                          quality="720p", media_type="video", output_format=None,
                          playlist_index=None, plan_limits_snapshot={})
    session = MagicMock()
    session.__enter__.return_value.execute.return_value.scalar_one_or_none.return_value = job
    monkeypatch.setattr(worker, "WorkerSessionLocal", lambda: session)
    monkeypatch.setattr(worker, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(worker, "_set_processing", lambda _: True)
    monkeypatch.setattr(worker, "_check_job_control", lambda _: None)
    monkeypatch.setattr(worker, "_get_job_status", lambda _: DownloadJobStatus.PROCESSING)
    failed = MagicMock()
    monkeypatch.setattr(worker, "_set_failed", failed)
    retry = MagicMock(side_effect=AssertionError("A source cooldown must not enter Celery retries"))
    monkeypatch.setattr(worker.download_task, "retry", retry)
    (tmp_path / "123.mp4.part").write_bytes(b"partial")
    guard.rate_limit_error(throttled(), URL)
    with pytest.raises(guard.InstagramRateLimited):
        worker.download_task.run(123)
    retry.assert_not_called()
    failed.assert_called_once_with(job_id=123, error_message="instagram_rate_limited")
    assert not (tmp_path / "123.mp4.part").exists()


@pytest.mark.skipif(os.getenv("RUN_REDIS_INTEGRATION") != "1", reason="Redis integration is enabled in CI")
def test_real_redis_deadline_is_shared_atomic_and_expires(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import uuid
    key = "mediahub:test:instagram:" + uuid.uuid4().hex
    monkeypatch.setattr(guard, "COOLDOWN_KEY", key)
    monkeypatch.setattr(guard, "_local_until", 0.0)
    monkeypatch.setattr(guard, "_redis_retry_at", 0.0)
    with guard.redis.Redis.from_url(os.environ["REDIS_URL"]) as client:
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                list(pool.map(guard._redis_operation, [60, 180, 30, 120, 90]))
            assert 175 <= client.ttl(key) <= 180
            assert guard.remaining_seconds() >= 175
            client.pexpire(key, 1)
            import time
            time.sleep(0.02)
            # A fresh process does not inherit an expired Redis deadline.
            monkeypatch.setattr(guard, "_local_until", 0.0)
            assert guard.remaining_seconds() == 0
        finally:
            client.delete(key)
