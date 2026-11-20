from __future__ import annotations

from app.security.redaction import is_sensitive_key, redact_sensitive, redact_value
from app.security.secrets.base import SecretValue


def test_sensitive_key_detection() -> None:
    assert is_sensitive_key("api_key") is True
    assert is_sensitive_key("access-token") is True
    assert is_sensitive_key("Authorization") is True
    assert is_sensitive_key("client_secret") is True
    assert is_sensitive_key("input_tokens") is False
    assert is_sensitive_key("output_tokens") is False
    assert is_sensitive_key("max_tokens") is False
    assert is_sensitive_key("token_count") is False
    assert is_sensitive_key("nested_field") is False


def test_redacts_nested_values() -> None:
    payload = {
        "api_key": "sk-secret",
        "nested": {
            "access_token": "token",
            "password": "pw",
            "input_tokens": 12,
            "items": [{"private_key": "pem", "name": "ok"}],
        },
    }

    redacted = redact_value(payload)

    assert redacted["api_key"] == "***"
    assert redacted["nested"]["access_token"] == "***"
    assert redacted["nested"]["password"] == "***"
    assert redacted["nested"]["input_tokens"] == 12
    assert redacted["nested"]["items"][0]["private_key"] == "***"
    assert redacted["nested"]["items"][0]["name"] == "ok"


def test_secret_value_is_never_exposed() -> None:
    value = SecretValue("super-secret")

    assert redact_value({"value": value})["value"] == "***"
    assert "super-secret" not in repr(value)
    assert "super-secret" not in str(value)


def test_structlog_processor() -> None:
    event = {"event": "call", "authorization": "Bearer secret", "input_tokens": 3}

    processed = redact_sensitive(None, "info", event)

    assert processed["authorization"] == "***"
    assert processed["input_tokens"] == 3
