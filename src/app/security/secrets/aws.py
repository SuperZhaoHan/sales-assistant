from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from app.security.secrets.base import SecretError, SecretProvider, SecretValue


class AWSSecretsManagerProvider(SecretProvider):
    """AWS Secrets Manager provider.

    Requires the optional ``aws`` extra: ``pip install -e '.[aws]'``.
    The secret name is the AWS secret id. For JSON secrets, use
    ``secret-name#field`` to select a field.
    """

    def __init__(self, region: str | None = None) -> None:
        self._region = region
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SecretError("boto3 is not installed; install the aws extra") from exc
        self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def _read(self, name: str) -> SecretValue | None:
        client = self._get_client()
        secret_id, _, field = name.partition("#")
        try:
            response = client.get_secret_value(SecretId=secret_id)
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundException":
                return None
            raise SecretError("AWS Secrets Manager read failed") from exc

        if "SecretString" in response:
            raw = response["SecretString"]
            if field:
                import json

                try:
                    raw = json.loads(raw)[field]
                except (ValueError, KeyError) as exc:
                    raise SecretError("secret field not found") from exc
            return SecretValue(str(raw))
        if "SecretBinary" in response:
            return SecretValue(bytes(response["SecretBinary"]).decode("utf-8"))
        return None

    async def get(self, name: str) -> SecretValue | None:
        return await asyncio.to_thread(self._read, name)

    async def get_many(self, names: Sequence[str]) -> dict[str, SecretValue]:
        result: dict[str, SecretValue] = {}
        for name in names:
            value = await self.get(name)
            if value is not None:
                result[name] = value
        return result
