import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import redis

from app.services import source_capacity as capacity
from app.services.download import DownloadService


@pytest.fixture
def storage(monkeypatch):
    if os.getenv("RUN_REDIS_INTEGRATION") != "1":
        pytest.skip("Real Redis admission tests run in CI")
    prefix = "mediahub:test-capacity:" + uuid4().hex + ":"
    monkeypatch.setattr(capacity, "PREFIX", prefix)
    values = {"downloads.metadata_seconds": 120, "downloads.metadata_parallel": 2,
              "downloads.instagram_interval_ms": 250, "downloads.reuse_minutes": 60}
    monkeypatch.setattr(capacity, "configuration", lambda: values)
    with capacity.storage() as client:
        assert client.ping()
        yield client
        keys = list(client.scan_iter(prefix + "*"))
        if keys:
            client.delete(*keys)


def test_redis_outage_does_not_create_an_upstream_burst(monkeypatch):
    monkeypatch.setattr(capacity, "configuration", lambda: {"downloads.metadata_seconds": 120,
        "downloads.metadata_parallel": 2, "downloads.instagram_interval_ms": 250})
    monkeypatch.setattr(capacity, "storage", lambda: (_ for _ in ()).throw(redis.ConnectionError("test outage")))
    monkeypatch.setattr(DownloadService, "get_media_info", lambda *_: pytest.fail("Must not fetch during Redis outage"))
    with pytest.raises(capacity.SourceBusy):
        capacity.media_info("https://www.instagram.com/reel/PublicTest/")
    with pytest.raises(capacity.SourceBusy):
        capacity.pace_instagram()


def test_many_identical_links_share_one_extraction_and_no_media_bytes(storage, monkeypatch):
    counter = []
    def extract(url, item=None):
        counter.append(url)
        time.sleep(0.6)
        return {"title": "Synthetic public post", "source_url": url, "formats": [], "media_type": "video"}
    monkeypatch.setattr(DownloadService, "get_media_info", extract)
    barrier = threading.Barrier(40)
    def fetch(_):
        barrier.wait(timeout=15)
        try:
            return capacity.media_info("https://www.instagram.com/reel/PublicTest/")
        except capacity.SourceBusy:
            return "BUSY"
    with ThreadPoolExecutor(max_workers=40) as pool:
        results = list(pool.map(fetch, range(40)))
    assert len(counter) == 1
    assert any(isinstance(value, dict) for value in results)
    assert all(value == "BUSY" or value["title"] == "Synthetic public post" for value in results)
    assert storage.zcard(capacity.PREFIX + "active") == 0
    data = list(storage.scan_iter(capacity.PREFIX + "info:*"))
    assert len(data) == 1 and len(storage.get(data[0])) < 500


def test_distinct_link_extractions_obey_shared_concurrency(storage, monkeypatch):
    state = {"running": 0, "peak": 0}
    lock = threading.Lock()
    def extract(url, item=None):
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.15)
        with lock:
            state["running"] -= 1
        return {"title": "Test", "source_url": url, "formats": []}
    monkeypatch.setattr(DownloadService, "get_media_info", extract)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda i: capacity.media_info(f"https://www.instagram.com/reel/TestPost{i}/"), range(12)))
    assert len(results) == 12 and state["peak"] <= 2


def test_nonpublic_social_metadata_is_scoped_to_the_requesting_user(storage, monkeypatch):
    calls = []
    def extract(url, item=None):
        calls.append(url)
        return {"title": "Unlisted test", "source_url": url, "formats": [], "_cache_public": False}
    monkeypatch.setattr(DownloadService, "get_media_info", extract)
    url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    capacity.media_info(url, telegram_id=100)
    capacity.media_info(url, telegram_id=100)
    capacity.media_info(url, telegram_id=200)
    assert len(calls) == 2 and not capacity.public_verified(url)


def test_explicit_public_visibility_enables_shared_reuse(storage, monkeypatch):
    monkeypatch.setattr(DownloadService, "get_media_info", lambda url, item=None:
        {"title": "Public test", "source_url": url, "formats": [], "_cache_public": True})
    url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    capacity.media_info(url, telegram_id=100)
    assert capacity.public_verified("https://youtu.be/AAAAAAAAAAA")


def test_request_spacing_is_shared_across_threads(storage, monkeypatch):
    from app.services import instagram_access
    monkeypatch.setattr(instagram_access, "check_instagram_access", lambda _: None)
    def acquire(_):
        capacity.pace_instagram()
        return time.monotonic()
    with ThreadPoolExecutor(max_workers=5) as pool:
        times = sorted(pool.map(acquire, range(5)))
    assert all(b - a >= 0.22 for a, b in zip(times, times[1:]))


def test_expired_owner_cannot_release_a_replacement_lease(storage):
    key, active = capacity.PREFIX + "lock:test", capacity.PREFIX + "active"
    assert storage.eval(capacity.ACQUIRE, 2, key, active, "old", 2) == 1
    storage.set(key, "new", ex=60)
    storage.eval(capacity.RELEASE, 2, key, active, "old")
    assert storage.get(key) == "new"
