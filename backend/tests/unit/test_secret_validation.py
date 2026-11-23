from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.core.config import load_config
from app.core.errors import ConfigurationError
from app.security.secrets.base import SecretProvider, SecretValue
from app.security.secrets.factory import validate_required_secrets
from app.security.secrets.resolver import SecretResolver


class EmptyProvider(SecretProvider):
    async def get(self, name: str) -> SecretValue | None:
        return None

    async def get_many(self, names: Sequence[str]) -> dict[str, SecretValue]:
        return {}


async def test_llm_key_is_checked_in_test_environment() -> None:
    settings = load_config("test").model_copy(deep=True)
    settings.llm.provider = "anthropic"
    settings.llm.api_key_ref = "secret://llm/anthropic_api_key"
    resolver = SecretResolver(EmptyProvider(), cache_ttl_seconds=0)

    # test is permissive: a missing key logs a warning but does not abort.
    await validate_required_secrets(settings, resolver)


async def test_llm_key_missing_fails_in_staging() -> None:
    settings = load_config("staging").model_copy(deep=True)
    settings.secrets.required = []
    settings.secrets.provider = "env"
    settings.redis.enabled = False
    resolver = SecretResolver(EmptyProvider(), cache_ttl_seconds=0)

    with pytest.raises(ConfigurationError):
        await validate_required_secrets(settings, resolver)
