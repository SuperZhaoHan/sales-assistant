from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEY_PATTERN = re.compile(
    r"(^|[_-])(passwords?|passwd|secrets?|api[_-]?key|authorization|cookies?|"
    r"private[_-]?key|credentials?|signature|session[_-]?id)($|[_-])"
    r"|(^|[_-])(access|refresh|id|auth)?[_-]?token($|[_-])",
    re.IGNORECASE,
)
SAFE_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "max_tokens",
    "token_count",
    "tokens",
}
MASK = "***"


def is_sensitive_key(key: str, extra_keys: set[str] | None = None) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in SAFE_KEYS:
        return False
    if extra_keys and normalized in extra_keys:
        return True
    return bool(SENSITIVE_KEY_PATTERN.search(normalized))


def _is_secret_value(value: Any) -> bool:
    # Local import avoids a module-level cycle with app.security.secrets.base.
    try:
        from app.security.secrets.base import SecretValue
    except ImportError:  # pragma: no cover - defensive during partial imports
        return False
    return isinstance(value, SecretValue)


def redact_value(value: Any, extra_keys: set[str] | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            key: MASK if is_sensitive_key(str(key), extra_keys) else redact_value(item, extra_keys)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_value(item, extra_keys) for item in value]
    if _is_secret_value(value):
        return MASK
    return value


def sanitize_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    redacted = redact_value(details)
    if not isinstance(redacted, Mapping):
        return {}
    return {str(key): value for key, value in redacted.items()}


def redact_sensitive(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
    extra_keys: set[str] | None = None,
) -> dict[str, Any]:
    """structlog processor that masks sensitive keys and SecretValue objects."""

    del logger, method_name
    redacted = redact_value(event_dict, extra_keys)
    return redacted if isinstance(redacted, dict) else {}
