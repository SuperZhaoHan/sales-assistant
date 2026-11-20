from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol


class IdempotencyStore(Protocol):
    async def get(self, key: str) -> Any | None: ...

    async def set(self, key: str, value: Any, ttl_seconds: int = 86400) -> None: ...

    async def delete(self, key: str) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at <= time.monotonic():
                self._entries.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        async with self._lock:
            self._entries[key] = (value, time.monotonic() + ttl_seconds)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._entries.pop(key, None)


class RedisIdempotencyStore:
    """Shared idempotency store for multi-replica deployments."""

    def __init__(self, client: Any, prefix: str = "risk:idem:") -> None:
        self._client = client
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(self._key(key))
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl_seconds: int = 86400) -> None:
        await self._client.setex(self._key(key), ttl_seconds, json.dumps(value))

    async def delete(self, key: str) -> None:
        await self._client.delete(self._key(key))
