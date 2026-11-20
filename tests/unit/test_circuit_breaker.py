from __future__ import annotations

import asyncio

from app.risk.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState


async def test_circuit_breaker_opens_and_recovers() -> None:
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.01)

    assert await breaker.allow_request() is True

    await breaker.record_failure()
    assert await breaker.allow_request() is True

    await breaker.record_failure()
    assert await breaker.allow_request() is False

    await asyncio.sleep(0.02)
    assert await breaker.allow_request() is True
    assert breaker.state is CircuitState.HALF_OPEN
    assert await breaker.allow_request() is False

    await breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert await breaker.allow_request() is True


async def test_half_open_failure_reopens() -> None:
    breaker = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.01)
    await breaker.record_failure()

    await asyncio.sleep(0.02)
    assert await breaker.allow_request() is True
    await breaker.record_failure()

    assert breaker.state is CircuitState.OPEN
    assert await breaker.allow_request() is False


def test_registry_reuses_breakers() -> None:
    registry = CircuitBreakerRegistry()

    assert registry.get("provider-a") is registry.get("provider-a")
    assert registry.get("provider-a") is not registry.get("provider-b")
