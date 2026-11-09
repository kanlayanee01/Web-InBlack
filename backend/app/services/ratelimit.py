"""Sliding-window rate limiting shared across replicas through Redis.

A sliding window counts the actual last N seconds. A fixed per-minute bucket would allow double
the limit in a burst across a minute boundary.

If Redis is unavailable each replica falls back to its own counters, so limits stay in force,
just per replica, instead of the endpoint either opening up or going down.
"""

import secrets
import time
from collections import deque

import redis.asyncio as redis

from app.problem import Problem

_REDIS_FAILURES = (redis.RedisError, OSError, TimeoutError)
_MAX_LOCAL_KEYS = 50_000

# Returns 0 when allowed, otherwise the milliseconds until a slot frees up. Runs atomically.
_SLIDING_WINDOW = """
local time = redis.call('TIME')
local now = time[1] * 1000 + math.floor(time[2] / 1000)
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    return math.max(tonumber(oldest[2]) + window - now, 1)
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return 0
"""


class RateLimiter:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        self._local: dict[str, deque[int]] = {}

    def _check_local(self, key: str, limit: int, window_ms: int) -> int:
        if len(self._local) > _MAX_LOCAL_KEYS:
            self._local.clear()
        now = time.monotonic_ns() // 1_000_000
        hits = self._local.setdefault(key, deque())
        while hits and hits[0] <= now - window_ms:
            hits.popleft()
        if len(hits) >= limit:
            return max(hits[0] + window_ms - now, 1)
        hits.append(now)
        return 0

    async def _wait_ms(self, key: str, limit: int, window_ms: int) -> int:
        try:
            result = await self._client.eval(  # type: ignore[misc]
                _SLIDING_WINDOW, 1, f"rl:{key}", str(limit), str(window_ms), secrets.token_hex(8)
            )
            return int(result)
        except _REDIS_FAILURES:
            return self._check_local(key, limit, window_ms)

    async def enforce(self, key: str, limit: int, window_seconds: int) -> None:
        wait_ms = await self._wait_ms(key, limit, window_seconds * 1000)
        if wait_ms > 0:
            raise Problem(
                429,
                "RATE_LIMITED",
                "Too many requests, please try again later",
                {"Retry-After": str((wait_ms + 999) // 1000)},
            )
