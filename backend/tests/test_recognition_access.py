from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis

from app.services import recognition_access as access, source_capacity
from app.services.media_tool_providers import ToolError
from .test_source_capacity import storage


@pytest.fixture
def coordination(storage, monkeypatch):
    monkeypatch.setattr(access, "PREFIX", source_capacity.PREFIX + "recognition:")
    monkeypatch.setattr(access, "configuration", lambda: {"downloads.recognition_interval_ms": 250})
    return storage


def test_429_stops_later_calls_without_blocking_other_provider(coordination):
    access.record_limit("shazamio", {"Retry-After": "120"})
    assert 115 <= coordination.ttl(access._keys("shazamio")[1]) <= 120
    for operation in (access.check, access.before_request):
        with pytest.raises(ToolError, match="provider_busy"):
            operation("shazamio")
    access.check("audd")
    access.before_request("audd")


def test_retry_after_date_and_concurrent_limits_preserve_longer_wait(coordination):
    access.record_limit("shazamio", {"retry-after": format_datetime(datetime.now(timezone.utc) + timedelta(seconds=180))})
    access.record_limit("shazamio", {"Retry-After": "30"})
    assert 175 <= coordination.ttl(access._keys("shazamio")[1]) <= 180
    coordination.delete(access._keys("shazamio")[1])
    access.record_limit("shazamio", {"Retry-After": "invalid"})
    assert 55 <= coordination.ttl(access._keys("shazamio")[1]) <= 60


def test_recognition_requests_obey_shared_spacing(coordination):
    def call(_):
        access.before_request("shazamio")
        return time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        times = sorted(pool.map(call, range(4)))
    assert all(b - a >= 0.22 for a, b in zip(times, times[1:]))


def test_recognition_does_not_burst_during_coordination_outage(monkeypatch):
    monkeypatch.setattr(access, "storage", lambda: (_ for _ in ()).throw(redis.ConnectionError("test outage")))
    monkeypatch.setattr(access, "configuration", lambda: {"downloads.recognition_interval_ms": 250})
    for operation in (access.check, access.before_request):
        with pytest.raises(ToolError, match="provider_busy"):
            operation("shazamio")
