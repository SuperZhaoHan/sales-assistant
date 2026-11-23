from __future__ import annotations

from typing import Any

from app.core.error_codes import ErrorCode, get_error_spec
from app.security.redaction import sanitize_details


class AppError(Exception):
    """Base class for all expected application errors.

    ``message`` is safe to return to the caller. Internal details belong in
    ``details`` only after sanitization, or in logs.
    """

    default_code: str | ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | ErrorCode | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
        retryable: bool | None = None,
        http_status: int | None = None,
    ) -> None:
        spec = get_error_spec(code or self.default_code)
        self.code = spec.code
        self.spec = spec
        self.message = message or spec.message
        self.details = details or {}
        self.retryable = spec.retryable if retryable is None else retryable
        self.cause = cause
        self._http_status = http_status if http_status is not None else spec.http_status
        super().__init__(self.message)
        if cause is not None:
            self.__cause__ = cause

    @property
    def http_status(self) -> int:
        return self._http_status

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": sanitize_details(self.details),
            "retryable": self.retryable,
        }

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ValidationError(AppError):
    default_code = ErrorCode.VALIDATION_ERROR


class AuthenticationError(AppError):
    default_code = ErrorCode.AUTHENTICATION_ERROR


class PermissionDeniedError(AppError):
    default_code = ErrorCode.PERMISSION_DENIED


class NotFoundError(AppError):
    default_code = ErrorCode.NOT_FOUND


class ConflictError(AppError):
    default_code = ErrorCode.CONFLICT


class BusinessRuleError(AppError):
    default_code = ErrorCode.BUSINESS_RULE


class RateLimitError(AppError):
    default_code = ErrorCode.RATE_LIMIT


class ExternalServiceError(AppError):
    default_code = ErrorCode.EXTERNAL_SERVICE

    def __init__(
        self,
        message: str | None = None,
        *,
        provider: str | None = None,
        operation: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        cause: BaseException | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if provider:
            details["provider"] = provider
        if operation:
            details["operation"] = operation
        if status_code is not None:
            details["status_code"] = status_code
        super().__init__(
            message,
            details=details,
            retryable=retryable,
            cause=cause,
        )


class UpstreamTimeoutError(AppError):
    default_code = ErrorCode.TIMEOUT


class ConfigurationError(AppError):
    default_code = ErrorCode.CONFIGURATION_ERROR


class InternalError(AppError):
    default_code = ErrorCode.INTERNAL_ERROR
