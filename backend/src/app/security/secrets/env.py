from __future__ import annotations

import os
from collections.abc import Sequence

from app.security.secrets.base import SecretProvider, SecretValue


class EnvSecretProvider(SecretProvider):
    """Read secrets from environment variables.

    ``llm/anthropic_api_key`` becomes ``LLM_ANTHROPIC_API_KEY`` by default.
    Use this for local development and CI only; staging/prod should use a
    managed backend.
    """

    def __init__(self, prefix: str = "") -> None:
        self._prefix = prefix

    def env_name(self, name: str) -> str:
        normalized = name.replace("/", "_").replace("-", "_").replace(".", "_").upper()
        return f"{self._prefix}{normalized}"

    async def get(self, name: str) -> SecretValue | None:
        raw = os.getenv(self.env_name(name))
        return SecretValue(raw) if raw else None

    async def get_many(self, names: Sequence[str]) -> dict[str, SecretValue]:
        result: dict[str, SecretValue] = {}
        for name in names:
            value = await self.get(name)
            if value is not None:
                result[name] = value
        return result
