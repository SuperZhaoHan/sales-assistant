from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import AppConfig, load_config


def test_loads_test_environment() -> None:
    settings = load_config("test")

    assert settings.env == "test"
    assert settings.llm.provider == "fake"
    assert settings.logging.json_format is False
    assert settings.server.docs_enabled is False
    assert settings.risk.enabled is False


def test_loads_staging_with_hardening() -> None:
    settings = load_config("staging")

    assert settings.env == "staging"
    assert settings.logging.json_format is True
    assert settings.server.docs_enabled is False
    assert settings.secrets.provider == "file"
    assert settings.secrets.required == ["llm/anthropic_api_key"]
    assert settings.llm.provider == "anthropic"
    assert settings.llm.allowed_models


def test_loads_prod_with_hardening() -> None:
    settings = load_config("prod")

    assert settings.env == "prod"
    assert settings.llm.provider == "anthropic"
    assert settings.risk.fail_closed is True
    assert settings.secrets.provider == "file"
    assert settings.llm.api_key_ref and settings.llm.api_key_ref.startswith("secret://")


def test_env_override_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP__LOGGING__LEVEL", "WARNING")
    monkeypatch.setenv("APP__RISK__RATE_LIMIT__REQUESTS", "7")

    settings = load_config("test")

    assert settings.logging.level == "WARNING"
    assert settings.risk.rate_limit.requests == 7


def test_unknown_field_is_rejected() -> None:
    data = load_config("test").model_dump()
    data["unknown_section"] = {"enabled": True}

    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_prod_rejects_docs_enabled() -> None:
    data = load_config("prod").model_dump()
    data["server"]["docs_enabled"] = True

    with pytest.raises(ValidationError, match="docs_enabled"):
        AppConfig.model_validate(data)


def test_prod_rejects_wildcard_cors() -> None:
    data = load_config("prod").model_dump()
    data["server"]["cors_origins"] = ["*"]

    with pytest.raises(ValidationError, match="cors_origins"):
        AppConfig.model_validate(data)


def test_prod_rejects_fake_llm() -> None:
    data = load_config("prod").model_dump()
    data["llm"]["provider"] = "fake"

    with pytest.raises(ValidationError, match="fake LLM"):
        AppConfig.model_validate(data)


def test_prod_rejects_env_secret_provider() -> None:
    data = load_config("prod").model_dump()
    data["secrets"]["provider"] = "env"

    with pytest.raises(ValidationError, match="secret provider"):
        AppConfig.model_validate(data)
