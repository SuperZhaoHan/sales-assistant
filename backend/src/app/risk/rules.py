from __future__ import annotations

import re
from typing import Protocol

from app.risk.models import RiskContext, RiskResult
from app.risk.quota import UsageStore, usage_period
from app.risk.rate_limit import RateLimiter


class RiskRule(Protocol):
    name: str

    async def evaluate(self, context: RiskContext) -> RiskResult | None: ...


class KillSwitchRule:
    name = "kill_switch"

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        del context
        if self.enabled:
            return RiskResult(
                rule=self.name,
                allowed=False,
                reason="kill_switch",
                risk_score=1.0,
            )
        return None


class ModelAllowlistRule:
    name = "model_allowlist"

    def __init__(self, allowed_models: list[str], enabled: bool = True) -> None:
        self._allowed = set(allowed_models)
        self._enabled = enabled

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        if not self._enabled or not self._allowed or context.model is None:
            return None
        if context.model not in self._allowed:
            return RiskResult(
                rule=self.name,
                allowed=False,
                reason="model_not_allowed",
                risk_score=0.8,
                metadata={"model": context.model},
            )
        return None


class MaxInputLengthRule:
    name = "max_input_length"

    def __init__(self, max_chars: int) -> None:
        self._max_chars = max_chars

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        if len(context.input_text) > self._max_chars:
            return RiskResult(
                rule=self.name,
                allowed=False,
                reason="input_too_long",
                risk_score=0.5,
                metadata={"input_chars": len(context.input_text), "max_chars": self._max_chars},
            )
        return None


class ContentSafetyRule:
    name = "content_safety"

    def __init__(self, blocked_terms: list[str]) -> None:
        self._blocked_terms = [term.lower() for term in blocked_terms if term]

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        if not self._blocked_terms:
            return None
        lowered = context.input_text.lower()
        for term in self._blocked_terms:
            if term in lowered:
                return RiskResult(
                    rule=self.name,
                    allowed=False,
                    reason="content_blocked",
                    risk_score=0.9,
                    metadata={"term": term},
                )
        return None


class PromptInjectionRule:
    name = "prompt_injection"

    _PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
        re.compile(
            r"disregard\s+(the\s+)?(system|developer)\s+(prompt|message|instructions)", re.I
        ),
        re.compile(
            r"reveal\s+(the\s+)?(system\s+prompt|developer\s+message|hidden\s+instructions)", re.I
        ),
        re.compile(r"print\s+(your\s+)?(system\s+prompt|instructions)", re.I),
        re.compile(r"you\s+are\s+now\s+(dan|developer\s+mode|jailbroken|unrestricted)", re.I),
        re.compile(r"<\|?(system|im_start)\|?>", re.I),
        re.compile(r"忽略(之前|以上|前面|所有)的?(指令|要求|提示词)"),
        re.compile(r"泄露(系统|开发者)提示词"),
    ]

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        if not self._enabled:
            return None
        for pattern in self._PATTERNS:
            if pattern.search(context.input_text):
                return RiskResult(
                    rule=self.name,
                    allowed=False,
                    reason="prompt_injection",
                    risk_score=0.95,
                    metadata={"pattern": pattern.pattern},
                )
        return None


class RateLimitRule:
    name = "rate_limit"

    def __init__(
        self,
        limiter: RateLimiter,
        requests: int,
        window_seconds: int,
        enabled: bool = True,
    ) -> None:
        self._limiter = limiter
        self._requests = requests
        self._window_seconds = window_seconds
        self._enabled = enabled

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        if not self._enabled:
            return None
        allowed = await self._limiter.allow(
            context.usage_key,
            limit=self._requests,
            window_seconds=self._window_seconds,
        )
        if not allowed:
            return RiskResult(
                rule=self.name,
                allowed=False,
                reason="rate_limit_exceeded",
                risk_score=0.7,
                metadata={
                    "limit": self._requests,
                    "window_seconds": self._window_seconds,
                },
            )
        return None


class QuotaRule:
    name = "quota"

    def __init__(
        self,
        store: UsageStore,
        daily_requests: int,
        daily_tokens: int,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._daily_requests = daily_requests
        self._daily_tokens = daily_tokens
        self._enabled = enabled

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        if not self._enabled:
            return None
        usage = await self._store.get(context.usage_key, usage_period())
        if usage["requests"] >= self._daily_requests:
            return RiskResult(
                rule=self.name,
                allowed=False,
                reason="quota_requests_exceeded",
                risk_score=0.7,
                metadata={"daily_requests": self._daily_requests},
            )
        if usage["tokens"] + context.estimated_tokens > self._daily_tokens:
            return RiskResult(
                rule=self.name,
                allowed=False,
                reason="quota_tokens_exceeded",
                risk_score=0.7,
                metadata={"daily_tokens": self._daily_tokens},
            )
        return None


class BudgetRule:
    name = "budget"

    def __init__(
        self,
        store: UsageStore,
        daily_budget_usd: float,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._daily_budget_usd = daily_budget_usd
        self._enabled = enabled

    async def evaluate(self, context: RiskContext) -> RiskResult | None:
        if not self._enabled:
            return None
        usage = await self._store.get(context.budget_key, usage_period())
        if usage["cost_usd"] >= self._daily_budget_usd:
            return RiskResult(
                rule=self.name,
                allowed=False,
                reason="budget_exceeded",
                risk_score=0.6,
                metadata={"daily_budget_usd": self._daily_budget_usd},
            )
        return None
