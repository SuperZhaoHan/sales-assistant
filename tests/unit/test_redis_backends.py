from __future__ import annotations

from typing import Any

from app.risk.quota import RedisUsageStore, usage_period
from app.risk.rate_limit import RedisRateLimiter


class FakeRedisCounter:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl


async def test_redis_rate_limiter() -> None:
    limiter = RedisRateLimiter(FakeRedisCounter())

    assert await limiter.allow("key", limit=2, window_seconds=60) is True
    assert await limiter.allow("key", limit=2, window_seconds=60) is True
    assert await limiter.allow("key", limit=2, window_seconds=60) is False


class FakePipeline:
    def __init__(self, client: FakeRedisUsage) -> None:
        self.client = client
        self.operations: list[tuple[Any, ...]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def hincrby(self, key: str, field: str, amount: int) -> FakePipeline:
        self.operations.append(("hincrby", key, field, amount))
        return self

    def hincrbyfloat(self, key: str, field: str, amount: float) -> FakePipeline:
        self.operations.append(("hincrbyfloat", key, field, amount))
        return self

    def expire(self, key: str, ttl: int) -> FakePipeline:
        self.operations.append(("expire", key, ttl))
        return self

    async def execute(self) -> list[object]:
        for operation in self.operations:
            if operation[0] == "hincrby":
                _, key, field, amount = operation
                hashes = self.client.hashes.setdefault(key, {})
                hashes[field] = str(int(hashes.get(field, "0")) + amount)
            elif operation[0] == "hincrbyfloat":
                _, key, field, amount = operation
                hashes = self.client.hashes.setdefault(key, {})
                hashes[field] = str(float(hashes.get(field, "0")) + amount)
            elif operation[0] == "expire":
                _, key, ttl = operation
                self.client.expires[key] = ttl
        return []


class FakeRedisUsage:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        del transaction
        return FakePipeline(self)


async def test_redis_usage_store() -> None:
    client = FakeRedisUsage()
    store = RedisUsageStore(client)
    period = usage_period()

    await store.add("u1:llm.complete", period, requests=1, tokens=10, cost_usd=0.25)
    usage = await store.get("u1:llm.complete", period)

    assert usage["requests"] == 1.0
    assert usage["tokens"] == 10.0
    assert usage["cost_usd"] == 0.25
    assert client.expires
