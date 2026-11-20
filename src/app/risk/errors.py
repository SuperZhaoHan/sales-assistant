from __future__ import annotations

from app.core.error_codes import ErrorCode, ErrorSpec, register_error_spec
from app.core.errors import AppError, RateLimitError
from app.risk.models import RiskDecision

CIRCUIT_OPEN_CODE = "RISK_4008"
register_error_spec(
    ErrorSpec(CIRCUIT_OPEN_CODE, 503, "依赖服务熔断中", retryable=True, severity="warning")
)


class RiskDeniedError(AppError):
    default_code = ErrorCode.RISK_DENIED


class KillSwitchError(AppError):
    default_code = ErrorCode.KILL_SWITCH


class PromptInjectionError(AppError):
    default_code = ErrorCode.PROMPT_INJECTION


class ContentBlockedError(AppError):
    default_code = ErrorCode.CONTENT_BLOCKED


class BudgetExceededError(AppError):
    default_code = ErrorCode.BUDGET_EXCEEDED


class CircuitOpenError(AppError):
    default_code = CIRCUIT_OPEN_CODE


def decision_to_error(decision: RiskDecision) -> AppError:
    reasons = set(decision.reasons)

    if reasons & {"rate_limit_exceeded", "quota_requests_exceeded", "quota_tokens_exceeded"}:
        return RateLimitError("请求过于频繁或额度不足", details={"reasons": sorted(reasons)})
    if "kill_switch" in reasons:
        return KillSwitchError(details={"reasons": sorted(reasons)})
    if "prompt_injection" in reasons:
        return PromptInjectionError(details={"reasons": sorted(reasons)})
    if "content_blocked" in reasons:
        return ContentBlockedError(details={"reasons": sorted(reasons)})
    if "budget_exceeded" in reasons:
        return BudgetExceededError(details={"reasons": sorted(reasons)})
    return RiskDeniedError(details={"reasons": sorted(reasons)})
