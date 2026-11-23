from __future__ import annotations

import asyncio
import time
from enum import StrEnum

from app.observability.metrics import set_circuit_state


class CircuitState(StrEnum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


_STATE_VALUES = {
    CircuitState.CLOSED: 0,
    CircuitState.HALF_OPEN: 1,
    CircuitState.OPEN: 2,
}


class CircuitBreaker:
    """Simple async circuit breaker for one external dependency."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self._name = name
        self._failure_threshold = max(failure_threshold, 1)
        self._recovery_timeout = max(recovery_timeout, 0.0)
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_probe_in_flight = False
        self._lock = asyncio.Lock()
        set_circuit_state(name, _STATE_VALUES[self._state])

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        return self._state

    def _set_state(self, state: CircuitState) -> None:
        self._state = state
        set_circuit_state(self._name, _STATE_VALUES[state])

    async def allow_request(self) -> bool:
        async with self._lock:
            if self._state is CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self._recovery_timeout:
                    self._set_state(CircuitState.HALF_OPEN)
                    self._half_open_probe_in_flight = True
                    return True
                return False
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_probe_in_flight:
                    return False
                self._half_open_probe_in_flight = True
                return True
            return True

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._half_open_probe_in_flight = False
            if self._state is not CircuitState.CLOSED:
                self._set_state(CircuitState.CLOSED)

    async def record_failure(self) -> None:
        async with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._open()
                return
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open()

    def _open(self) -> None:
        self._failures = self._failure_threshold
        self._opened_at = time.monotonic()
        self._half_open_probe_in_flight = False
        self._set_state(CircuitState.OPEN)


class CircuitBreakerRegistry:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                failure_threshold=self._failure_threshold,
                recovery_timeout=self._recovery_timeout,
            )
            self._breakers[name] = breaker
        return breaker
