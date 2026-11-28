"""Positive link-recognition metadata and Telegram preview IDs, never bytes."""
import hashlib
import json
from uuid import uuid4

import redis

from app.core.config import settings
from app.services.download_capacity import source_identity
from app.services.source_capacity import configuration, public_verified, storage

PREFIX = "mediahub:music-links:v1:"
RESULT_FIELDS = {"title", "artist", "album", "release_date", "duration", "recognition_provider",
                 "recognition_key", "candidates", "automatic_candidate", "recognized_artist", "artist_correction"}


def key(source_url, provider, telegram_id):
    identity = source_identity(source_url) if source_url else None
    if not identity or not configuration()["downloads.metadata_seconds"]:
        return None
    scope = "public" if public_verified(source_url) else f"user:{telegram_id}"
    return hashlib.sha256(json.dumps([settings.telegram_bot_token, provider, identity, scope]).encode()).hexdigest()


def read(cache_key):
    if not cache_key:
        return None
    try:
        with storage() as client:
            raw = client.get(PREFIX + "result:" + cache_key)
            if raw:
                result = json.loads(raw)
                preview = client.get(PREFIX + "preview:" + cache_key)
                return result, json.loads(preview) if preview else None
    except (redis.RedisError, OSError, ValueError):
        pass
    return None


def acquire(cache_key):
    if not cache_key:
        return None
    try:
        with storage() as client:
            owner = uuid4().hex
            if client.set(PREFIX + "lock:" + cache_key, owner, nx=True, ex=360):
                return owner
            return False
    except (redis.RedisError, OSError):
        return None


def release(cache_key, owner):
    if not cache_key or not owner:
        return
    try:
        with storage() as client:
            client.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0",
                        1, PREFIX + "lock:" + cache_key, owner)
    except (redis.RedisError, OSError):
        pass


def _store(name, data):
    encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded.encode()) > 64 * 1024:
        return
    try:
        with storage() as client:
            client.eval("""
local clock = redis.call('TIME')
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[2], clock[1], KEYS[1])
local extra = redis.call('ZCARD', KEYS[2])-512
if extra > 0 then
  for _, old in ipairs(redis.call('ZRANGE', KEYS[2], 0, extra-1)) do
    redis.call('DEL', old); redis.call('ZREM', KEYS[2], old)
  end
end
redis.call('EXPIRE', KEYS[2], 1200)
return 1
""", 2, PREFIX + name, PREFIX + "index", encoded, max(1, configuration()["downloads.metadata_seconds"]))
    except (redis.RedisError, OSError):
        pass


def save(cache_key, result):
    if cache_key and result and result.get("title") and result.get("candidates"):
        _store("result:" + cache_key, {k: v for k, v in result.items() if k in RESULT_FIELDS})


def save_preview(cache_key, preview, file_id):
    if cache_key and isinstance(file_id, str) and 0 < len(file_id) <= 1024 and preview:
        _store("preview:" + cache_key, {k: v for k, v in {**preview, "telegram_file_id": file_id}.items()
                                       if k in {"width", "height", "duration", "telegram_file_id"}})
