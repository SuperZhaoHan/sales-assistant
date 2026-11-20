from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import replace

import structlog

from app.core.config import LLMConfig
from app.llm.base import (
    LLMChunk,
    LLMProvider,
    LLMProviderError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMServiceError,
    LLMUsage,
    estimate_tokens,
)
from app.llm.cost import CostCalculator
from app.observability.metrics import record_llm_request, record_llm_usage
from app.risk.audit import AuditLogger
from app.risk.circuit_breaker import CircuitBreakerRegistry
from app.risk.engine import RiskEngine
from app.risk.errors import CircuitOpenError, decision_to_error
from app.risk.models import RiskContext, RiskSubject
from app.security.masking import mask_pii

logger = structlog.get_logger(__name__)


class LLMGateway:
    """The only legal path from business code to a third-party LLM."""

    def __init__(
        self,
        provider: LLMProvider,
        config: LLMConfig,
        risk_engine: RiskEngine,
        breakers: CircuitBreakerRegistry,
        calculator: CostCalculator,
        audit: AuditLogger | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._risk = risk_engine
        self._breakers = breakers
        self._calculator = calculator
        self._audit = audit or AuditLogger()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def _prepare(self, request: LLMRequest) -> LLMRequest:
        system = request.system
        if system is None and self._config.system_prompt:
            system = self._config.system_prompt

        max_tokens = min(request.max_tokens, self._config.max_output_tokens)
        messages = request.messages
        if self._config.pii_masking_enabled:
            messages = tuple(
                replace(message, content=mask_pii(message.content)) for message in request.messages
            )

        if (
            system == request.system
            and max_tokens == request.max_tokens
            and messages is request.messages
        ):
            return request
        return replace(request, system=system, max_tokens=max_tokens, messages=messages)

    @staticmethod
    def _context(request: LLMRequest, subject: RiskSubject, action: str) -> RiskContext:
        input_text = request.input_text()
        return RiskContext(
            subject=subject,
            action=action,
            model=request.model,
            input_text=input_text,
            estimated_tokens=estimate_tokens(input_text),
            metadata=dict(request.metadata),
        )

    async def complete(self, request: LLMRequest, subject: RiskSubject) -> LLMResponse:
        request = self._prepare(request)
        context = self._context(request, subject, "llm.complete")

        decision = await self._risk.evaluate(context)
        if not decision.allowed:
            raise decision_to_error(decision)

        breaker = self._breakers.get(self._provider.name)
        if not await breaker.allow_request():
            raise CircuitOpenError(details={"provider": self._provider.name})

        started = time.perf_counter()
        last_error: LLMProviderError | None = None

        for attempt in range(self._config.max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._provider.complete(request),
                    timeout=request.timeout_seconds or self._config.timeout_seconds,
                )
            except TimeoutError as exc:
                last_error = LLMProviderError(
                    "provider timeout",
                    provider=self._provider.name,
                    retryable=True,
                    cause=exc,
                )
            except LLMProviderError as exc:
                last_error = exc
            except LLMRefusalError:
                await breaker.record_success()
                raise
            else:
                await breaker.record_success()
                return await self._finalize(response, context, started)

            await breaker.record_failure()
            if last_error is None or not last_error.retryable:
                break
            if attempt >= self._config.max_retries:
                break
            await asyncio.sleep(self._config.retry_backoff_seconds * (2**attempt))

        self._record_failure(context, last_error, started)
        raise LLMServiceError(
            details={
                "provider": self._provider.name,
                "status_code": last_error.status_code if last_error else None,
            },
            retryable=last_error.retryable if last_error else False,
            cause=last_error,
        )

    async def stream(self, request: LLMRequest, subject: RiskSubject) -> AsyncIterator[LLMChunk]:
        request = self._prepare(request)
        context = self._context(request, subject, "llm.stream")

        decision = await self._risk.evaluate(context)
        if not decision.allowed:
            raise decision_to_error(decision)

        breaker = self._breakers.get(self._provider.name)
        if not await breaker.allow_request():
            raise CircuitOpenError(details={"provider": self._provider.name})

        started = time.perf_counter()
        collected: list[str] = []
        final_usage: LLMUsage | None = None
        yielded = False
        last_error: LLMProviderError | None = None

        for attempt in range(self._config.max_retries + 1):
            try:
                async for chunk in self._provider.stream(request):
                    if chunk.delta:
                        yielded = True
                        collected.append(chunk.delta)
                        yield chunk
                    if chunk.done:
                        final_usage = chunk.usage
            except LLMRefusalError:
                await breaker.record_success()
                raise
            except LLMProviderError as exc:
                last_error = exc
                await breaker.record_failure()
                if yielded or not exc.retryable or attempt >= self._config.max_retries:
                    break
                await asyncio.sleep(self._config.retry_backoff_seconds * (2**attempt))
                continue
            else:
                await breaker.record_success()
                usage = final_usage or LLMUsage(
                    input_tokens=estimate_tokens(request.input_text()),
                    output_tokens=estimate_tokens("".join(collected)),
                )
                usage = await self._finalize_usage(context, request.model, usage, started)
                yield LLMChunk(delta="", done=True, usage=usage)
                return

        self._record_failure(context, last_error, started)
        raise LLMServiceError(
            details={
                "provider": self._provider.name,
                "status_code": last_error.status_code if last_error else None,
            },
            retryable=last_error.retryable if last_error else False,
            cause=last_error,
        )

    async def _finalize(
        self,
        response: LLMResponse,
        context: RiskContext,
        started: float,
    ) -> LLMResponse:
        usage = await self._finalize_usage(
            context,
            response.model,
            response.usage,
            started,
            provider=response.provider,
        )
        return replace(response, usage=usage)

    async def _finalize_usage(
        self,
        context: RiskContext,
        model: str,
        usage: LLMUsage,
        started: float,
        *,
        provider: str | None = None,
    ) -> LLMUsage:
        provider_name = provider or self._provider.name
        cost = self._calculator.calculate(model, usage.input_tokens, usage.output_tokens)
        usage = usage.with_cost(cost)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        record_llm_request(provider_name, model, "success")
        record_llm_usage(provider_name, model, usage.input_tokens, usage.output_tokens, cost)
        try:
            await self._risk.record_usage(
                context,
                tokens=usage.total_tokens,
                cost_usd=cost,
            )
        except Exception:
            logger.exception("failed_to_record_llm_usage")

        self._audit.log(
            "llm_request",
            provider=provider_name,
            model=model,
            action=context.action,
            subject_id=context.subject.subject_id,
            tenant_id=context.subject.tenant_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=round(cost, 8),
            duration_ms=duration_ms,
        )
        return usage

    def _record_failure(
        self,
        context: RiskContext,
        error: LLMProviderError | None,
        started: float,
    ) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        record_llm_request(self._provider.name, context.model or "", "error")
        self._audit.log(
            "llm_request_failed",
            provider=self._provider.name,
            model=context.model,
            action=context.action,
            subject_id=context.subject.subject_id,
            tenant_id=context.subject.tenant_id,
            error_type=type(error).__name__ if error else "unknown",
            status_code=error.status_code if error else None,
            retryable=error.retryable if error else False,
            duration_ms=duration_ms,
        )

    async def aclose(self) -> None:
        await self._provider.aclose()
