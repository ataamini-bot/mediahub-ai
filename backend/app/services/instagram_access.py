"""Back off shared public Instagram access after an upstream HTTP 429."""
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import os
import re
import threading
import time
from urllib.parse import urlsplit

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
import yt_dlp


logger = logging.getLogger(__name__)
COOLDOWN_KEY = "mediahub:instagram:cooldown"
DEFAULT_COOLDOWN_SECONDS = 15 * 60
_local_until = 0.0
_redis_retry_at = 0.0
_lock = threading.Lock()
_EXTEND_COOLDOWN = """
local current = redis.call('TTL', KEYS[1])
local seconds = tonumber(ARGV[1])
if current < seconds then
    redis.call('SET', KEYS[1], '1', 'EX', seconds)
    return seconds
end
return current
"""


class InstagramRateLimited(RuntimeError):
    code = "instagram_rate_limited"

    def __init__(self, retry_after=DEFAULT_COOLDOWN_SECONDS):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(self.code)

    def detail(self):
        return {"code": self.code, "retry_after": self.retry_after}


def is_instagram_url(value):
    try:
        host = (urlsplit(str(value)).hostname or "").lower()
    except ValueError:
        return False
    return host == "instagram.com" or host.endswith(".instagram.com")


def _redis_operation(seconds=None):
    """Redis shares the deadline across API, workers and sample subprocesses."""
    global _redis_retry_at
    if time.monotonic() < _redis_retry_at:
        return 0
    try:
        with redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            socket_connect_timeout=0.5, socket_timeout=0.5,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            return int(client.ttl(COOLDOWN_KEY) if seconds is None else
                       client.eval(_EXTEND_COOLDOWN, 1, COOLDOWN_KEY, seconds))
    except (redis.RedisError, ValueError, OSError):
        # Retain an in-process deadline during a Redis outage. Never include
        # connection strings, post URLs or upstream response bodies in logs.
        _redis_retry_at = time.monotonic() + 5
        logger.warning("Instagram cooldown storage unavailable; using local backoff")
        return 0


def remaining_seconds():
    global _local_until
    shared = _redis_operation()
    with _lock:
        if shared > 0:
            _local_until = max(_local_until, time.monotonic() + shared)
        local = math.ceil(_local_until - time.monotonic())
    return max(0, local, shared)


def check_instagram_access(source_url):
    if is_instagram_url(source_url):
        remaining = remaining_seconds()
        if remaining:
            raise InstagramRateLimited(remaining)


def _errors(exc):
    pending, seen = [exc], set()
    while pending and len(seen) < 16:
        error = pending.pop()
        if not isinstance(error, BaseException) or id(error) in seen:
            continue
        seen.add(id(error))
        yield error
        pending.extend((error.__cause__, error.__context__, getattr(error, "cause", None)))
        info = getattr(error, "exc_info", None)
        if isinstance(info, tuple) and len(info) > 1:
            pending.append(info[1])


def _retry_after(error):
    response = getattr(error, "response", None)
    headers = getattr(error, "headers", None) or getattr(response, "headers", None) or {}
    value = str(headers.get("Retry-After", headers.get("retry-after", ""))).strip()
    try:
        if value.isdigit():
            seconds = int(value)
        else:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds = math.ceil((deadline - datetime.now(timezone.utc)).total_seconds())
        if 0 < seconds <= 2**31 - 1:
            return seconds
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def rate_limit_error(exc, source_url):
    """Translate only Instagram throttling, and never extend an existing pause."""
    global _local_until
    if not is_instagram_url(source_url):
        return None
    errors = list(_errors(exc))
    for error in errors:
        if isinstance(error, InstagramRateLimited):
            return error
    for error in errors:
        if str(error) == InstagramRateLimited.code:
            return InstagramRateLimited(remaining_seconds() or DEFAULT_COOLDOWN_SECONDS)
    limited = [error for error in errors if
               getattr(error, "status", None) == 429 or getattr(error, "code", None) == 429 or
               re.search(r"\bHTTP(?: Error)?\s+429\b|\b429:\s*Too Many Requests\b", str(error), re.I)]
    if not limited:
        return None
    seconds = max((_retry_after(error) or 0 for error in limited), default=0) or DEFAULT_COOLDOWN_SECONDS
    with _lock:
        _local_until = max(_local_until, time.monotonic() + seconds)
    seconds = max(seconds, _redis_operation(seconds))
    with _lock:
        _local_until = max(_local_until, time.monotonic() + seconds)
    logger.warning("Instagram upstream rate limit; cooldown_seconds=%s", seconds)
    return InstagramRateLimited(seconds)


@contextmanager
def media_downloader(source_url, options):
    """Use the existing yt-dlp profile; stop before its fallback or retry on 429."""
    instagram = is_instagram_url(source_url)
    check_instagram_access(source_url)
    try:
        with yt_dlp.YoutubeDL(dict(options)) as downloader:
            if instagram:
                original_urlopen = downloader.urlopen

                def guarded_urlopen(request):
                    check_instagram_access(source_url)
                    try:
                        return original_urlopen(request)
                    except Exception as exc:
                        limited = rate_limit_error(exc, source_url)
                        if limited:
                            raise limited from None
                        raise

                # This instance alone is guarded; other platforms and sessions
                # retain their own networking and retry behavior.
                downloader.urlopen = guarded_urlopen
            yield downloader
    except Exception as exc:
        limited = rate_limit_error(exc, source_url)
        if limited:
            raise limited from None
        raise
