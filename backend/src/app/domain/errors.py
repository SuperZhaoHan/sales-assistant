from __future__ import annotations

from app.core.error_codes import ErrorCode
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError


class InsufficientBalanceError(BusinessRuleError):
    default_code = ErrorCode.INSUFFICIENT_BALANCE


class InvalidStateError(ConflictError):
    default_code = ErrorCode.INVALID_STATE


class UserNotFoundError(NotFoundError):
    default_code = ErrorCode.USER_NOT_FOUND
