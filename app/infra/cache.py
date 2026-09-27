"""Redis: кэш, идемпотентность, лимиты.

item = cache.get_json(f"item:{id}")                 # None, если нет
cache.set_json(f"item:{id}", data, ttl=60)
if not cache.once(f"idem:{key}"): ...                # повтор запроса (SET NX)
if not cache.rate_limit(f"rl:{user}", 10, 60): ...   # >10 запросов за 60 с
"""

import json
from typing import Any

import redis
import redis.asyncio

from app.settings import settings

r = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=1)
ar = redis.asyncio.Redis.from_url(settings.redis_url, decode_responses=True)  # для async-кода


def get_json(key: str) -> Any | None:
    raw = r.get(key)
    return None if raw is None else json.loads(raw)


def set_json(key: str, value: Any, ttl: int = 60) -> None:
    r.set(key, json.dumps(value, default=str), ex=ttl)


def delete(*keys: str) -> None:
    r.delete(*keys)


def once(key: str, ttl: int = 86400) -> bool:
    """True — первый раз (ключ поставлен), False — уже был."""
    return bool(r.set(key, "1", nx=True, ex=ttl))


def rate_limit(key: str, limit: int, window_s: int) -> bool:
    """Фиксированное окно: True — можно, False — лимит исчерпан."""
    n = r.incr(key)
    if n == 1:
        r.expire(key, window_s)
    return n <= limit
