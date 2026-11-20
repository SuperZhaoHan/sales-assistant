from __future__ import annotations

import asyncio
from typing import Any

from app.risk.idempotency import InMemoryIdempotencyStore, RedisIdempotencyStore


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        assert ttl > 0
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


async def test_in_memory_idempotency_store() -> None:
    store = InMemoryIdempotencyStore()

    assert await store.get("key") is None
    await store.set("key", {"result": "ok"}, ttl_seconds=60)
    assert await store.get("key") == {"result": "ok"}

    await store.delete("key")
    assert await store.get("key") is None


async def test_in_memory_idempotency_expiry() -> None:
    store = InMemoryIdempotencyStore()
    await store.set("key", {"result": "ok"}, ttl_seconds=0)

    await asyncio.sleep(0.001)

    assert await store.get("key") is None


async def test_redis_idempotency_store() -> None:
    client = FakeRedis()
    store = RedisIdempotencyStore(client)

    value: dict[str, Any] = {"result": "ok", "n": 1}
    await store.set("key", value, ttl_seconds=60)

    assert await store.get("key") == value
    assert client.data

    await store.delete("key")
    assert await store.get("key") is None
