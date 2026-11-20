from __future__ import annotations

from typing import Any

from app.core.config import AppConfig
from app.core.errors import ConfigurationError


def build_redis_client(settings: AppConfig) -> Any | None:
    if not settings.redis.enabled:
        return None
    try:
        import redis.asyncio as redis
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ConfigurationError(
            "redis is enabled but the redis extra is not installed; run pip install -e '.[redis]'"
        ) from exc
    return redis.from_url(settings.redis.url, decode_responses=True)
