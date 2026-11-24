"""Short-lived public metadata and source cooldowns, never media/user identifiers."""
from collections import OrderedDict
import json
import os
import threading
import time

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

PREFIX = "mediahub:music-sources:v1:"
_local = OrderedDict()
_lock = threading.Lock()
_redis_until = 0.0


def _remote(operation):
    global _redis_until
    if time.monotonic() < _redis_until:
        return None
    try:
        with redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"),
                socket_connect_timeout=.3, socket_timeout=.3, retry=Retry(NoBackoff(), 0), decode_responses=True) as client:
            return operation(client)
    except (redis.RedisError, OSError, ValueError):
        _redis_until = time.monotonic() + 5
        return None


def read(keys):
    values = _remote(lambda client: client.mget([PREFIX + key for key in keys]))
    now = time.monotonic()
    result = {}
    with _lock:
        for index, key in enumerate(keys):
            if values is not None and values[index] is not None:
                try:
                    result[key] = json.loads(values[index])
                except (TypeError, ValueError):
                    pass
            elif key in _local:
                until, value = _local[key]
                if until > now:
                    result[key] = value
                else:
                    del _local[key]
    return result


def write(key, value, seconds):
    with _lock:
        _local[key] = (time.monotonic() + seconds, value)
        _local.move_to_end(key)
        while len(_local) > 128:
            _local.popitem(last=False)
    _remote(lambda client: client.set(PREFIX + key, json.dumps(value), ex=seconds))
