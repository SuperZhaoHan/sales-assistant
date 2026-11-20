from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.security.secrets.base import (
    SecretError,
    SecretNotFoundError,
    SecretProvider,
    SecretRef,
    SecretValue,
)
from app.security.secrets.env import EnvSecretProvider
from app.security.secrets.file import FileSecretProvider
from app.security.secrets.resolver import SecretResolver


class CountingProvider(SecretProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.values = {"llm/anthropic_api_key": "sk-test"}

    async def get(self, name: str) -> SecretValue | None:
        self.calls += 1
        value = self.values.get(name)
        return SecretValue(value) if value is not None else None

    async def get_many(self, names: Sequence[str]) -> dict[str, SecretValue]:
        result: dict[str, SecretValue] = {}
        for name in names:
            value = await self.get(name)
            if value is not None:
                result[name] = value
        return result


def test_secret_value_is_opaque() -> None:
    value = SecretValue("sk-test")

    assert value.reveal() == "sk-test"
    assert repr(value) == "SecretValue('***')"
    assert str(value) == "***"
    assert bool(value) is True
    assert len(value) == len("sk-test")


def test_secret_ref_parsing() -> None:
    ref = SecretRef.parse("secret://llm/anthropic_api_key")

    assert ref.path == "llm/anthropic_api_key"
    assert ref.raw == "secret://llm/anthropic_api_key"

    with pytest.raises(ValueError):
        SecretRef.parse("llm/anthropic_api_key")
    with pytest.raises(ValueError):
        SecretRef.parse("secret://")


async def test_env_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ANTHROPIC_API_KEY", "sk-env")
    provider = EnvSecretProvider()

    value = await provider.get("llm/anthropic_api_key")

    assert value is not None
    assert value.reveal() == "sk-env"


async def test_file_provider_and_traversal(tmp_path: Path) -> None:
    base = tmp_path / "secrets"
    (base / "llm").mkdir(parents=True)
    (base / "llm" / "anthropic_api_key").write_text("sk-file\n", encoding="utf-8")
    provider = FileSecretProvider(base)

    value = await provider.get("llm/anthropic_api_key")

    assert value is not None
    assert value.reveal() == "sk-file"

    with pytest.raises(SecretError):
        await provider.get("../outside")
    with pytest.raises(SecretError):
        await provider.get("/etc/passwd")


async def test_resolver_caches_and_refreshes() -> None:
    provider = CountingProvider()
    resolver = SecretResolver(provider, cache_ttl_seconds=60)

    first = await resolver.resolve("secret://llm/anthropic_api_key")
    second = await resolver.resolve("secret://llm/anthropic_api_key")

    assert first.reveal() == second.reveal() == "sk-test"
    assert provider.calls == 1

    await resolver.refresh()
    await resolver.resolve("secret://llm/anthropic_api_key")

    assert provider.calls == 2


async def test_resolver_missing_secret() -> None:
    resolver = SecretResolver(CountingProvider(), cache_ttl_seconds=0)

    with pytest.raises(SecretNotFoundError):
        await resolver.resolve("secret://missing/secret")

    assert await resolver.resolve_optional("secret://missing/secret") is None
