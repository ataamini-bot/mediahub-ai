"""Shared request pacing and bounded metadata coalescing (no media storage)."""
from contextlib import contextmanager
import hashlib
import json
import logging
import threading
import time
from uuid import uuid4

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import select

from app.core.config import settings
from app.models.app_setting import ApplicationSetting
from app.services.download_capacity import public_source_key, source_identity
from app.services.managed_settings import MANAGED_SETTINGS, validate_managed_setting

logger = logging.getLogger(__name__)
PREFIX = "mediahub:source:v1:"
_config_until = 0.0
_config = {}
_config_lock = threading.Lock()
_metadata_threads = threading.BoundedSemaphore(24)


class SourceBusy(RuntimeError):
    code = "download_queue_full"

    def __init__(self):
        super().__init__(self.code)

    def detail(self):
        return {"code": self.code, "retry_after": 10}


def configuration():
    global _config_until, _config
    with _config_lock:
        if time.monotonic() < _config_until:
            return dict(_config)
        values = {key: item.default for key, item in MANAGED_SETTINGS.items() if key.startswith("downloads.")}
        try:
            from app.db.worker_session import WorkerSessionLocal
            with WorkerSessionLocal() as db:
                rows = db.scalars(select(ApplicationSetting).where(ApplicationSetting.key.in_(values))).all()
                for row in rows:
                    values[row.key] = validate_managed_setting(row.key, row.value_json)
        except Exception:
            logger.warning("Source capacity settings unavailable; using conservative defaults")
        _config, _config_until = values, time.monotonic() + 5
        return dict(values)


def storage():
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=1,
                                retry=Retry(NoBackoff(), 0), decode_responses=True)


def _proof_key(source_url):
    identity = source_identity(source_url)
    if identity is None:
        return None
    return PREFIX + "public:" + hashlib.sha256((settings.telegram_bot_token + identity).encode()).hexdigest()


def public_verified(source_url):
    if public_source_key(source_url):
        return True
    key = _proof_key(source_url)
    if key:
        try:
            with storage() as client:
                return client.get(key) == "1"
        except (redis.RedisError, OSError):
            pass
    return False


def register_public_source(source_url):
    """Called only after the extractor explicitly reports public availability."""
    key = _proof_key(source_url)
    if key:
        try:
            with storage() as client:
                client.eval("""
local clock = redis.call('TIME')
redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
redis.call('ZADD', KEYS[2], clock[1], KEYS[1])
local excess = redis.call('ZCARD', KEYS[2])-2000
if excess > 0 then
  for _, old in ipairs(redis.call('ZRANGE', KEYS[2], 0, excess-1)) do
    redis.call('DEL', old); redis.call('ZREM', KEYS[2], old)
  end
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[1])*2)
return 1
""", 2, key, PREFIX + "public-index", configuration()["downloads.reuse_minutes"] * 60)
        except (redis.RedisError, OSError):
            pass


PACE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1])*1000 + math.floor(tonumber(clock[2])/1000)
local next = tonumber(redis.call('GET', KEYS[1]) or '0')
if next > now then return next-now end
redis.call('SET', KEYS[1], now+tonumber(ARGV[1]), 'PX', ARGV[1])
return 0
"""


def pace_instagram():
    from app.services.instagram_access import check_instagram_access
    interval = configuration()["downloads.instagram_interval_ms"]
    deadline = time.monotonic() + 15
    try:
        with storage() as client:
            while time.monotonic() < deadline:
                check_instagram_access("https://www.instagram.com/")
                wait = int(client.eval(PACE, 1, PREFIX + "instagram", interval))
                if wait <= 0:
                    return
                time.sleep(min(wait / 1000, 0.5))
    except (redis.RedisError, OSError):
        # Redis is already required for the queue. An outage must not release
        # every API/worker process into an uncoordinated upstream burst.
        raise SourceBusy() from None
    raise SourceBusy()


ACQUIRE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', 60)
redis.call('ZADD', KEYS[2], now+60, ARGV[1])
redis.call('EXPIRE', KEYS[2], 120)
return 1
"""
RENEW = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local clock = redis.call('TIME')
redis.call('EXPIRE', KEYS[1], 60)
redis.call('ZADD', KEYS[2], tonumber(clock[1])+60, ARGV[1])
redis.call('EXPIRE', KEYS[2], 120)
return 1
"""
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""
STORE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local clock = redis.call('TIME')
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('ZADD', KEYS[3], clock[1], KEYS[2])
local excess = redis.call('ZCARD', KEYS[3])-512
if excess > 0 then
  local old = redis.call('ZRANGE', KEYS[3], 0, excess-1)
  for _, key in ipairs(old) do redis.call('DEL', key); redis.call('ZREM', KEYS[3], key) end
end
redis.call('EXPIRE', KEYS[3], 1200)
return 1
"""


@contextmanager
def renew_lease(client, key, owner):
    stopped = threading.Event()
    def renew():
        while not stopped.wait(15):
            try:
                if not client.eval(RENEW, 2, key, PREFIX + "active", owner):
                    break
            except redis.RedisError:
                break
    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)
        try:
            client.eval(RELEASE, 2, key, PREFIX + "active", owner)
        except redis.RedisError:
            pass  # Expiring leases recover after an interrupted process.


def media_info(source_url, playlist_index=None, telegram_id=None):
    """At most 24 waiting threads per API process, with shared active slots.

    Public-only sources can share metadata. Other social links remain scoped
    to the requesting Telegram account unless the extractor explicitly proves
    public availability. Failures never become a cached success.
    """
    from app.services.download import DownloadService
    if not _metadata_threads.acquire(blocking=False):
        raise SourceBusy()
    try:
        config = configuration()
        source = source_identity(source_url)
        public = public_verified(source_url)
        scope = "public" if public else (f"user:{telegram_id}" if telegram_id else None)
        cacheable = bool(source and scope and config["downloads.metadata_seconds"])
        identity = [settings.telegram_bot_token, source, playlist_index or 1, scope] if cacheable else [uuid4().hex]
        digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        cache_key, lock_key = PREFIX + "info:" + digest, PREFIX + "lock:" + digest
        owner = uuid4().hex
        deadline = time.monotonic() + 12
        try:
            with storage() as client:
                while time.monotonic() < deadline:
                    cached = client.get(cache_key) if cacheable else None
                    if cached:
                        result = json.loads(cached)
                        result["source_url"] = source_url
                        logger.info("Public metadata reused")
                        return result
                    if client.eval(ACQUIRE, 2, lock_key, PREFIX + "active", owner, config["downloads.metadata_parallel"]):
                        with renew_lease(client, lock_key, owner):
                            # The previous owner may have published between GET
                            # and our lease acquisition.
                            cached = client.get(cache_key) if cacheable else None
                            if cached:
                                result = json.loads(cached)
                                result["source_url"] = source_url
                                return result
                            result = DownloadService.get_media_info(source_url, playlist_index)
                            if result.get("_cache_public") is True:
                                register_public_source(source_url)
                            proven = result.get("_cache_public") is True and source is not None
                            if cacheable or (proven and config["downloads.metadata_seconds"]):
                                encoded = json.dumps(result)
                                if len(encoded.encode()) <= 256 * 1024:
                                    targets = {cache_key} if cacheable else set()
                                    if proven:
                                        shared = [settings.telegram_bot_token, source, playlist_index or 1, "public"]
                                        targets.add(PREFIX + "info:" + hashlib.sha256(json.dumps(shared).encode()).hexdigest())
                                    for target in targets:
                                        client.eval(STORE, 3, lock_key, target, PREFIX + "info-index",
                                                    owner, encoded, config["downloads.metadata_seconds"])
                            return result
                    time.sleep(0.1)
        except (redis.RedisError, OSError):
            raise SourceBusy() from None
        raise SourceBusy()
    finally:
        _metadata_threads.release()
