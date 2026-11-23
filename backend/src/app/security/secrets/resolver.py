from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from app.security.secrets.base import (
    SecretError,
    SecretNotFoundError,
    SecretProvider,
    SecretRef,
    SecretValue,
)


@dataclass
class _CacheEntry:
    value: SecretValue
    expires_at: float


class SecretResolver:
    """Resolve ``secret://`` references with a small in-process TTL cache."""

    def __init__(self, provider: SecretProvider, cache_ttl_seconds: int = 300) -> None:
        self._provider = provider
        self._cache_ttl = max(cache_ttl_seconds, 0)
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def resolve(self, ref: str | SecretRef) -> SecretValue:
        if isinstance(ref, str):
            try:
                secret_ref = SecretRef.parse(ref)
            except ValueError as exc:
                raise SecretError("invalid secret reference") from exc
        else:
            secret_ref = ref
        now = time.monotonic()
        cached = self._cache.get(secret_ref.path)
        if cached is not None and cached.expires_at > now:
            return cached.value

        async with self._lock:
            cached = self._cache.get(secret_ref.path)
            if cached is not None and cached.expires_at > now:
                return cached.value
            value = await self._provider.get(secret_ref.path)
            if value is None:
                raise SecretNotFoundError(secret_ref.path)
            if self._cache_ttl > 0:
                self._cache[secret_ref.path] = _CacheEntry(
                    value=value,
                    expires_at=now + self._cache_ttl,
                )
            return value

    async def resolve_optional(self, ref: str | SecretRef) -> SecretValue | None:
        try:
            return await self.resolve(ref)
        except SecretNotFoundError:
            return None

    async def resolve_many(self, refs: Sequence[str | SecretRef]) -> dict[str, SecretValue]:
        result: dict[str, SecretValue] = {}
        for ref in refs:
            key = ref.raw if isinstance(ref, SecretRef) else ref
            result[key] = await self.resolve(ref)
        return result

    async def refresh(self) -> None:
        async with self._lock:
            self._cache.clear()
            await self._provider.refresh()
