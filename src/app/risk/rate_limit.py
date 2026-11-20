from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Protocol


class RateLimiter(Protocol):
    async def allow(self, key: str, limit: int, window_seconds: int) -> bool: ...


class InMemoryRateLimiter:
    """Sliding-window limiter for a single process (local/test)."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


class RedisRateLimiter:
    """Fixed-window limiter shared by all replicas.

    Requires the optional ``redis`` extra.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        bucket = int(time.time() // window_seconds)
        redis_key = f"risk:rl:{key}:{bucket}"
        count = int(await self._client.incr(redis_key))
        if count == 1:
            await self._client.expire(redis_key, window_seconds + 1)
        return count <= limit
