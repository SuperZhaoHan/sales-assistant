from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable machine-readable error codes.

    The numeric suffix is part of the public contract. Never renumber an
    existing code; add a new one instead.
    """

    VALIDATION_ERROR = "VALIDATION_1001"
    AUTHENTICATION_ERROR = "AUTH_1001"
    PERMISSION_DENIED = "AUTH_1002"
    NOT_FOUND = "NOT_FOUND_1001"
    CONFLICT = "CONFLICT_1001"

    BUSINESS_RULE = "BIZ_1000"
    INSUFFICIENT_BALANCE = "BIZ_2001"
    INVALID_STATE = "BIZ_2002"
    USER_NOT_FOUND = "BIZ_2003"

    RATE_LIMIT = "RISK_4001"
    RISK_DENIED = "RISK_4002"
    KILL_SWITCH = "RISK_4003"
    PROMPT_INJECTION = "RISK_4004"
    CONTENT_BLOCKED = "RISK_4005"
    BUDGET_EXCEEDED = "RISK_4006"
    IDEMPOTENCY_CONFLICT = "RISK_4007"

    LLM_PROVIDER = "LLM_3001"
    LLM_REFUSAL = "LLM_3002"

    EXTERNAL_SERVICE = "EXT_5001"
    TIMEOUT = "INFRA_5002"
    INTERNAL_ERROR = "INFRA_5001"
    CONFIGURATION_ERROR = "INFRA_5003"

    # Stable error codes, not credentials.
    SECRET_NOT_FOUND = "SEC_6001"  # nosec B105
    SECRET_PROVIDER = "SEC_6002"  # nosec B105


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    http_status: int
    message: str
    retryable: bool = False
    severity: str = "error"
    alert: bool = False


ERROR_SPECS: dict[str, ErrorSpec] = {
    ErrorCode.VALIDATION_ERROR.value: ErrorSpec(
        ErrorCode.VALIDATION_ERROR.value, 400, "请求参数校验失败"
    ),
    ErrorCode.AUTHENTICATION_ERROR.value: ErrorSpec(
        ErrorCode.AUTHENTICATION_ERROR.value, 401, "未认证"
    ),
    ErrorCode.PERMISSION_DENIED.value: ErrorSpec(
        ErrorCode.PERMISSION_DENIED.value, 403, "无权访问"
    ),
    ErrorCode.NOT_FOUND.value: ErrorSpec(ErrorCode.NOT_FOUND.value, 404, "资源不存在"),
    ErrorCode.CONFLICT.value: ErrorSpec(ErrorCode.CONFLICT.value, 409, "资源冲突"),
    ErrorCode.BUSINESS_RULE.value: ErrorSpec(
        ErrorCode.BUSINESS_RULE.value, 422, "业务规则校验失败"
    ),
    ErrorCode.INSUFFICIENT_BALANCE.value: ErrorSpec(
        ErrorCode.INSUFFICIENT_BALANCE.value, 422, "余额不足"
    ),
    ErrorCode.INVALID_STATE.value: ErrorSpec(
        ErrorCode.INVALID_STATE.value, 409, "当前状态不允许该操作"
    ),
    ErrorCode.USER_NOT_FOUND.value: ErrorSpec(ErrorCode.USER_NOT_FOUND.value, 404, "用户不存在"),
    ErrorCode.RATE_LIMIT.value: ErrorSpec(
        ErrorCode.RATE_LIMIT.value, 429, "请求过于频繁", retryable=True
    ),
    ErrorCode.RISK_DENIED.value: ErrorSpec(ErrorCode.RISK_DENIED.value, 403, "请求被风控拦截"),
    ErrorCode.KILL_SWITCH.value: ErrorSpec(
        ErrorCode.KILL_SWITCH.value, 503, "服务暂时不可用", retryable=True
    ),
    ErrorCode.PROMPT_INJECTION.value: ErrorSpec(
        ErrorCode.PROMPT_INJECTION.value, 403, "请求包含疑似提示词注入内容"
    ),
    ErrorCode.CONTENT_BLOCKED.value: ErrorSpec(
        ErrorCode.CONTENT_BLOCKED.value, 403, "内容安全策略拦截"
    ),
    ErrorCode.BUDGET_EXCEEDED.value: ErrorSpec(
        ErrorCode.BUDGET_EXCEEDED.value, 429, "额度已用完", retryable=False
    ),
    ErrorCode.IDEMPOTENCY_CONFLICT.value: ErrorSpec(
        ErrorCode.IDEMPOTENCY_CONFLICT.value, 409, "相同幂等键的请求正在处理"
    ),
    ErrorCode.LLM_PROVIDER.value: ErrorSpec(
        ErrorCode.LLM_PROVIDER.value, 502, "大模型服务调用失败", retryable=True
    ),
    ErrorCode.LLM_REFUSAL.value: ErrorSpec(
        ErrorCode.LLM_REFUSAL.value, 422, "大模型拒绝处理该请求"
    ),
    ErrorCode.EXTERNAL_SERVICE.value: ErrorSpec(
        ErrorCode.EXTERNAL_SERVICE.value, 502, "外部服务调用失败", retryable=True
    ),
    ErrorCode.TIMEOUT.value: ErrorSpec(ErrorCode.TIMEOUT.value, 504, "请求超时", retryable=True),
    ErrorCode.INTERNAL_ERROR.value: ErrorSpec(
        ErrorCode.INTERNAL_ERROR.value, 500, "服务内部错误", severity="critical", alert=True
    ),
    ErrorCode.CONFIGURATION_ERROR.value: ErrorSpec(
        ErrorCode.CONFIGURATION_ERROR.value,
        500,
        "配置错误",
        severity="critical",
        alert=True,
    ),
    ErrorCode.SECRET_NOT_FOUND.value: ErrorSpec(
        ErrorCode.SECRET_NOT_FOUND.value, 500, "必需密钥缺失", severity="critical", alert=True
    ),
    ErrorCode.SECRET_PROVIDER.value: ErrorSpec(
        ErrorCode.SECRET_PROVIDER.value, 503, "密钥服务不可用", retryable=True
    ),
}


def register_error_spec(spec: ErrorSpec) -> None:
    """Register a project-specific error code.

    This keeps business error codes extensible without changing the core enum.
    """

    ERROR_SPECS[spec.code] = spec


def get_error_spec(code: str | ErrorCode) -> ErrorSpec:
    resolved = code.value if isinstance(code, ErrorCode) else str(code)
    return ERROR_SPECS.get(
        resolved,
        ErrorSpec(resolved, 500, "服务内部错误", severity="critical", alert=True),
    )
