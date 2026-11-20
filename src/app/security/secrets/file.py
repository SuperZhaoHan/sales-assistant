from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.security.secrets.base import SecretError, SecretProvider, SecretValue


class FileSecretProvider(SecretProvider):
    """Read secrets from a mounted directory such as /run/secrets.

    Docker and Kubernetes can mount a secret per file. The provider also
    supports nested names (``llm/anthropic_api_key``) but rejects path
    traversal.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir).resolve()

    def _resolve(self, name: str) -> Path:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SecretError("invalid secret name")
        path = (self._base / candidate).resolve()
        if path != self._base and self._base not in path.parents:
            raise SecretError("invalid secret name")
        return path

    async def get(self, name: str) -> SecretValue | None:
        path = self._resolve(name)
        if not path.is_file():
            return None
        return SecretValue(path.read_text(encoding="utf-8").strip())

    async def get_many(self, names: Sequence[str]) -> dict[str, SecretValue]:
        result: dict[str, SecretValue] = {}
        for name in names:
            value = await self.get(name)
            if value is not None:
                result[name] = value
        return result
