from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from app.security.secrets.base import SecretError, SecretProvider, SecretValue


class VaultSecretProvider(SecretProvider):
    """HashiCorp Vault KV v2 provider.

    Requires the optional ``vault`` extra: ``pip install -e '.[vault]'``.
    A secret name such as ``llm/anthropic_api_key`` is read from the KV path
    ``llm`` and the field ``anthropic_api_key``.
    """

    def __init__(self, url: str | None, token: str, mount: str = "secret") -> None:
        if not url:
            raise SecretError("Vault URL is not configured")
        self._url = url
        self._token = token
        self._mount = mount
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import hvac
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SecretError("hvac is not installed; install the vault extra") from exc
        self._client = hvac.Client(url=self._url, token=self._token)
        return self._client

    def _read(self, name: str) -> SecretValue | None:
        client = self._get_client()
        if "/" in name:
            path, field = name.rsplit("/", 1)
        else:
            path, field = name, "value"
        try:
            response = client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=self._mount,
            )
        except Exception as exc:  # hvac raises several exception types
            if exc.__class__.__name__ == "InvalidPath":
                return None
            raise SecretError("Vault read failed") from exc
        data = response.get("data", {}).get("data", {})
        value = data.get(field)
        return SecretValue(str(value)) if value is not None else None

    async def get(self, name: str) -> SecretValue | None:
        return await asyncio.to_thread(self._read, name)

    async def get_many(self, names: Sequence[str]) -> dict[str, SecretValue]:
        result: dict[str, SecretValue] = {}
        for name in names:
            value = await self.get(name)
            if value is not None:
                result[name] = value
        return result
