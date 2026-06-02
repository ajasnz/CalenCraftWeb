"""Simple in-memory TTL cache for feed responses."""
import time
import threading

_lock  = threading.Lock()
_cache = {}
DEFAULT_TTL = int(300)   # seconds — override via FEED_CACHE_TTL env var


def _ttl():
    import os
    try:
        return int(os.getenv("FEED_CACHE_TTL", DEFAULT_TTL))
    except (TypeError, ValueError):
        return DEFAULT_TTL


def get(key):
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        value, expires = entry
        if time.monotonic() > expires:
            del _cache[key]
            return None
        return value


def set(key, value, ttl=None):
    with _lock:
        _cache[key] = (value, time.monotonic() + (ttl if ttl is not None else _ttl()))


def invalidate(key):
    with _lock:
        _cache.pop(key, None)


def clear():
    with _lock:
        _cache.clear()