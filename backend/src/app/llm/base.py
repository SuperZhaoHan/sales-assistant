from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from app.core.error_codes import ErrorCode
from app.core.errors import AppError

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[LLMMessage, ...]
    model: str
    system: str | None = None
    max_tokens: int = 4096
    temperature: float | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def input_text(self) -> str:
        return "\n".join(message.content for message in self.messages)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def with_cost(self, cost_usd: float) -> LLMUsage:
        return replace(self, cost_usd=cost_usd)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    usage: LLMUsage
    stop_reason: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMChunk:
    delta: str
    done: bool = False
    usage: LLMUsage | None = None


class LLMProviderError(Exception):
    """Internal transport-level error raised by provider adapters."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        retryable: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        self.cause = cause
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


class LLMServiceError(AppError):
    default_code = ErrorCode.LLM_PROVIDER


class LLMRefusalError(AppError):
    default_code = ErrorCode.LLM_REFUSAL


class LLMProvider(Protocol):
    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]: ...

    async def aclose(self) -> None: ...


def estimate_tokens(text: str) -> int:
    """Cheap, provider-independent token estimate for pre-flight checks."""

    if not text:
        return 0
    return max(1, len(text) // 4)
