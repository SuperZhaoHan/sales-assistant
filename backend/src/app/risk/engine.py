from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import structlog

from app.core.config import AppConfig
from app.observability.metrics import record_risk_decision
from app.risk.audit import AuditLogger
from app.risk.models import RiskContext, RiskDecision, RiskResult
from app.risk.quota import InMemoryUsageStore, RedisUsageStore, UsageStore, usage_period
from app.risk.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from app.risk.redis import build_redis_client
from app.risk.rules import (
    BudgetRule,
    ContentSafetyRule,
    KillSwitchRule,
    MaxInputLengthRule,
    ModelAllowlistRule,
    PromptInjectionRule,
    QuotaRule,
    RateLimitRule,
    RiskRule,
)

logger = structlog.get_logger(__name__)


class RiskEngine:
    """Ordered rule engine with fail-closed / fail-open behaviour."""

    def __init__(
        self,
        rules: Sequence[RiskRule],
        *,
        enabled: bool = True,
        fail_closed: bool = True,
        audit: AuditLogger | None = None,
        usage_store: UsageStore | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self._rules = list(rules)
        self._enabled = enabled
        self._fail_closed = fail_closed
        self._audit = audit or AuditLogger()
        self._usage_store = usage_store or InMemoryUsageStore()
        self._redis_client = redis_client

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def evaluate(self, context: RiskContext) -> RiskDecision:
        if not self._enabled:
            decision = RiskDecision(True, "allow", 0.0, (), "")
            return replace(decision, audit_id=self._audit.risk_decision(decision, context))

        results: list[RiskResult] = []
        for rule in self._rules:
            try:
                result = await rule.evaluate(context)
            except Exception:
                logger.exception("risk_rule_failed", rule=rule.name)
                result = (
                    RiskResult(
                        rule=rule.name,
                        allowed=False,
                        reason="risk_rule_failed",
                        risk_score=1.0,
                    )
                    if self._fail_closed
                    else None
                )

            if result is None:
                continue

            results.append(result)
            record_risk_decision("allow" if result.allowed else "deny", result.rule)
            if not result.allowed:
                decision = RiskDecision(
                    allowed=False,
                    decision="deny",
                    risk_score=max(item.risk_score for item in results),
                    reasons=tuple(item.reason for item in results),
                    audit_id="",
                    results=tuple(results),
                )
                return replace(decision, audit_id=self._audit.risk_decision(decision, context))

        decision = RiskDecision(
            allowed=True,
            decision="allow",
            risk_score=max((item.risk_score for item in results), default=0.0),
            reasons=tuple(item.reason for item in results),
            audit_id="",
            results=tuple(results),
        )
        return replace(decision, audit_id=self._audit.risk_decision(decision, context))

    async def record_usage(
        self,
        context: RiskContext,
        *,
        tokens: int,
        cost_usd: float,
    ) -> None:
        period = usage_period()
        await self._usage_store.add(
            context.usage_key,
            period,
            requests=1,
            tokens=tokens,
            cost_usd=cost_usd,
        )
        if cost_usd:
            await self._usage_store.add(
                context.budget_key,
                period,
                cost_usd=cost_usd,
            )

    async def aclose(self) -> None:
        if self._redis_client is not None:
            close = getattr(self._redis_client, "aclose", None)
            if close is not None:
                await close()


def build_risk_engine(settings: AppConfig) -> RiskEngine:
    redis_client = build_redis_client(settings)

    if not settings.risk.enabled:
        return RiskEngine(
            [],
            enabled=False,
            fail_closed=settings.risk.fail_closed,
            redis_client=redis_client,
        )

    limiter = RedisRateLimiter(redis_client) if redis_client is not None else InMemoryRateLimiter()
    usage_store: UsageStore = (
        RedisUsageStore(redis_client) if redis_client is not None else InMemoryUsageStore()
    )

    rules: list[RiskRule] = [
        KillSwitchRule(settings.risk.kill_switch),
        ModelAllowlistRule(
            settings.llm.allowed_models,
            enabled=settings.risk.model_allowlist_enabled,
        ),
        MaxInputLengthRule(min(settings.risk.max_input_chars, settings.llm.max_input_chars)),
        RateLimitRule(
            limiter,
            requests=settings.risk.rate_limit.requests,
            window_seconds=settings.risk.rate_limit.window_seconds,
            enabled=settings.risk.rate_limit.enabled,
        ),
        QuotaRule(
            usage_store,
            daily_requests=settings.risk.quota.daily_requests,
            daily_tokens=settings.risk.quota.daily_tokens,
            enabled=settings.risk.quota.enabled,
        ),
    ]

    if settings.llm.daily_budget_usd is not None:
        rules.append(
            BudgetRule(
                usage_store,
                daily_budget_usd=settings.llm.daily_budget_usd,
                enabled=settings.risk.budget_enabled,
            )
        )

    rules.extend(
        [
            ContentSafetyRule(settings.risk.blocked_terms),
            PromptInjectionRule(settings.risk.prompt_injection_enabled),
        ]
    )

    return RiskEngine(
        rules,
        enabled=True,
        fail_closed=settings.risk.fail_closed,
        usage_store=usage_store,
        redis_client=redis_client,
    )
