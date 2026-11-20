from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol


def usage_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class UsageStore(Protocol):
    async def get(self, key: str, period: str) -> dict[str, float]: ...

    async def add(
        self,
        key: str,
        period: str,
        *,
        requests: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None: ...


class InMemoryUsageStore:
    def __init__(self) -> None:
        self._usage: dict[tuple[str, str], dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str, period: str) -> dict[str, float]:
        async with self._lock:
            usage = self._usage.get((key, period), {})
            return {
                "requests": float(usage.get("requests", 0.0)),
                "tokens": float(usage.get("tokens", 0.0)),
                "cost_usd": float(usage.get("cost_usd", 0.0)),
            }

    async def add(
        self,
        key: str,
        period: str,
        *,
        requests: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        async with self._lock:
            usage = self._usage.setdefault((key, period), {})
            usage["requests"] = usage.get("requests", 0.0) + requests
            usage["tokens"] = usage.get("tokens", 0.0) + tokens
            usage["cost_usd"] = usage.get("cost_usd", 0.0) + cost_usd


class RedisUsageStore:
    """Shared usage counters for quota and budget checks.

    Requires the optional ``redis`` extra.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _key(key: str, period: str) -> str:
        return f"risk:usage:{period}:{key}"

    async def get(self, key: str, period: str) -> dict[str, float]:
        raw = await self._client.hgetall(self._key(key, period))
        return {
            "requests": float(raw.get("requests", 0.0)),
            "tokens": float(raw.get("tokens", 0.0)),
            "cost_usd": float(raw.get("cost_usd", 0.0)),
        }

    async def add(
        self,
        key: str,
        period: str,
        *,
        requests: int = 0,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        redis_key = self._key(key, period)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hincrby(redis_key, "requests", requests)
            pipe.hincrby(redis_key, "tokens", tokens)
            pipe.hincrbyfloat(redis_key, "cost_usd", cost_usd)
            pipe.expire(redis_key, 60 * 60 * 48)
            await pipe.execute()
