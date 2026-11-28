"""Coordinate recognition calls and honor upstream Retry-After without retries."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import time

import redis

from app.services.source_capacity import configuration, storage

PREFIX = "mediahub:recognition:v1:"
logger = logging.getLogger(__name__)


def _keys(provider):
    if provider not in {"shazamio", "audd"}:
        raise ValueError("Unsupported recognition provider")
    return PREFIX + provider + ":next", PREFIX + provider + ":cooldown"


def _busy():
    from app.services.media_tool_providers import ToolError
    return ToolError("provider_busy")


def check(provider):
    _, cooldown = _keys(provider)
    try:
        with storage() as client:
            if client.ttl(cooldown) > 0:
                raise _busy()
    except (redis.RedisError, OSError):
        raise _busy() from None


def before_request(provider):
    keys = _keys(provider)
    interval = configuration()["downloads.recognition_interval_ms"]
    deadline = time.monotonic() + 15
    try:
        with storage() as client:
            while time.monotonic() < deadline:
                wait = int(client.eval("""
local ttl = redis.call('TTL', KEYS[2])
if ttl > 0 then return -ttl end
local clock = redis.call('TIME')
local now = tonumber(clock[1])*1000 + math.floor(tonumber(clock[2])/1000)
local next = tonumber(redis.call('GET', KEYS[1]) or '0')
if next > now then return next-now end
redis.call('SET', KEYS[1], now+tonumber(ARGV[1]), 'PX', ARGV[1])
return 0
""", 2, *keys, interval))
                if wait < 0:
                    raise _busy()
                if wait == 0:
                    return
                time.sleep(min(wait / 1000, 0.5))
    except (redis.RedisError, OSError):
        raise _busy() from None
    raise _busy()


def record_limit(provider, headers):
    value = str(headers.get("Retry-After", headers.get("retry-after", ""))).strip()
    seconds = 60
    try:
        if value.isdigit():
            parsed = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            parsed = math.ceil((date - datetime.now(timezone.utc)).total_seconds())
        if 0 < parsed <= 2**31 - 1:
            seconds = parsed
    except (ValueError, TypeError, OverflowError):
        pass
    try:
        with storage() as client:
            client.eval("""
local ttl = redis.call('TTL', KEYS[1])
if ttl < tonumber(ARGV[1]) then redis.call('SET', KEYS[1], '1', 'EX', ARGV[1]) end
return 1
""", 1, _keys(provider)[1], seconds)
    except (redis.RedisError, OSError):
        pass  # New calls still fail closed if Redis coordination is unavailable.
    logger.warning("Recognition provider rate limited: provider=%s, cooldown_seconds=%s", provider, seconds)
