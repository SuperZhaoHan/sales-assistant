from __future__ import annotations

from app.core.error_codes import ErrorCode, ErrorSpec, register_error_spec
from app.core.errors import AppError, ExternalServiceError, ValidationError
from app.domain.errors import InsufficientBalanceError, InvalidStateError, UserNotFoundError


def test_business_error_public_dict() -> None:
    error = InsufficientBalanceError(details={"required": 100, "available": 10})
    payload = error.to_public_dict()

    assert payload["code"] == ErrorCode.INSUFFICIENT_BALANCE.value
    assert payload["message"] == "余额不足"
    assert payload["details"] == {"required": 100, "available": 10}
    assert error.http_status == 422
    assert error.retryable is False


def test_sensitive_details_are_masked() -> None:
    error = ValidationError(
        details={
            "api_key": "sk-secret",
            "nested": {"access_token": "token-value", "input_tokens": 10},
        }
    )

    details = error.to_public_dict()["details"]

    assert details["api_key"] == "***"
    assert details["nested"]["access_token"] == "***"
    assert details["nested"]["input_tokens"] == 10


def test_unknown_code_falls_back_to_500() -> None:
    error = AppError(code="CUSTOM_9999")

    assert error.http_status == 500
    assert error.code == "CUSTOM_9999"


def test_custom_error_spec_can_be_registered() -> None:
    register_error_spec(ErrorSpec("BIZ_9999", 418, "teapot"))

    error = AppError(code="BIZ_9999")

    assert error.http_status == 418
    assert error.message == "teapot"


def test_external_service_error_details() -> None:
    error = ExternalServiceError(
        provider="anthropic",
        operation="messages.create",
        status_code=529,
    )

    assert error.http_status == 502
    assert error.details == {
        "provider": "anthropic",
        "operation": "messages.create",
        "status_code": 529,
    }


def test_other_business_errors() -> None:
    assert InvalidStateError().http_status == 409
    assert UserNotFoundError().http_status == 404
    assert str(InsufficientBalanceError()) == "BIZ_2001: 余额不足"
