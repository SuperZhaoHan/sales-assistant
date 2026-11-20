from __future__ import annotations

import pytest

from app.core.config import LLMConfig
from app.llm.base import (
    LLMMessage,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMServiceError,
)
from app.llm.cost import CostCalculator
from app.llm.gateway import LLMGateway
from app.risk.circuit_breaker import CircuitBreakerRegistry
from app.risk.engine import RiskEngine
from app.risk.errors import CircuitOpenError, KillSwitchError
from app.risk.models import RiskSubject
from app.risk.rules import KillSwitchRule
from tests.factories.llm import make_llm_request
from tests.fakes.flaky_llm import FlakyLLMProvider


def make_gateway(
    provider,
    *,
    max_retries: int = 2,
    risk_engine: RiskEngine | None = None,
    breakers: CircuitBreakerRegistry | None = None,
    pii_masking_enabled: bool = False,
) -> LLMGateway:
    return LLMGateway(
        provider=provider,
        config=LLMConfig(
            model="test-model",
            max_retries=max_retries,
            retry_backoff_seconds=0.0,
            pii_masking_enabled=pii_masking_enabled,
        ),
        risk_engine=risk_engine or RiskEngine([], enabled=False),
        breakers=breakers or CircuitBreakerRegistry(),
        calculator=CostCalculator(),
    )


async def test_retries_retryable_error() -> None:
    provider = FlakyLLMProvider(failures=1)
    gateway = make_gateway(provider)

    response = await gateway.complete(make_llm_request(), RiskSubject("u1"))

    assert provider.calls == 2
    assert response.text == "ok"
    assert response.usage.cost_usd > 0
    assert response.usage.total_tokens == 15


async def test_does_not_retry_non_retryable_error() -> None:
    provider = FlakyLLMProvider(failures=1, retryable=False)
    gateway = make_gateway(provider)

    with pytest.raises(LLMServiceError) as exc_info:
        await gateway.complete(make_llm_request(), RiskSubject("u1"))

    assert exc_info.value.code == "LLM_3001"
    assert provider.calls == 1


async def test_circuit_opens_after_failures() -> None:
    provider = FlakyLLMProvider(failures=10)
    breakers = CircuitBreakerRegistry(failure_threshold=1, recovery_timeout=30)
    gateway = make_gateway(provider, max_retries=0, breakers=breakers)

    with pytest.raises(LLMServiceError) as first:
        await gateway.complete(make_llm_request(), RiskSubject("u1"))
    assert first.value.code == "LLM_3001"

    with pytest.raises(CircuitOpenError):
        await gateway.complete(make_llm_request(), RiskSubject("u1"))


async def test_risk_denial_stops_call() -> None:
    provider = FlakyLLMProvider()
    risk = RiskEngine([KillSwitchRule(True)], enabled=True, fail_closed=True)
    gateway = make_gateway(provider, risk_engine=risk)

    with pytest.raises(KillSwitchError):
        await gateway.complete(make_llm_request(), RiskSubject("u1"))

    assert provider.calls == 0


async def test_stream_success() -> None:
    provider = FlakyLLMProvider(response_text="hello stream")
    gateway = make_gateway(provider)

    chunks = [chunk async for chunk in gateway.stream(make_llm_request(), RiskSubject("u1"))]

    assert any(chunk.delta for chunk in chunks)
    final = chunks[-1]
    assert final.done is True
    assert final.usage is not None
    assert final.usage.cost_usd > 0


async def test_pii_masking_is_applied_before_provider_call() -> None:
    class CapturingProvider(FlakyLLMProvider):
        def __init__(self) -> None:
            super().__init__()
            self.requests: list[LLMRequest] = []

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            return await super().complete(request)

    provider = CapturingProvider()
    gateway = make_gateway(provider, pii_masking_enabled=True)
    request = LLMRequest(
        messages=(LLMMessage(role="user", content="email alice@example.com"),),
        model="test-model",
    )

    await gateway.complete(request, RiskSubject("u1"))

    assert "alice@example.com" not in provider.requests[0].messages[0].content
    assert "[EMAIL]" in provider.requests[0].messages[0].content


async def test_refusal_is_not_retried() -> None:
    class RefusingProvider(FlakyLLMProvider):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.calls += 1
            raise LLMRefusalError(details={"model": request.model})

    provider = RefusingProvider()
    gateway = make_gateway(provider)

    with pytest.raises(LLMRefusalError):
        await gateway.complete(make_llm_request(), RiskSubject("u1"))

    assert provider.calls == 1
