from __future__ import annotations

from collections.abc import AsyncIterator

from app.llm.base import (
    LLMChunk,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)


class FlakyLLMProvider(LLMProvider):
    name = "flaky"

    def __init__(
        self,
        *,
        failures: int = 0,
        retryable: bool = True,
        response_text: str = "ok",
    ) -> None:
        self.failures = failures
        self.retryable = retryable
        self.response_text = response_text
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise LLMProviderError(
                "provider failed",
                provider=self.name,
                status_code=503,
                retryable=self.retryable,
            )
        return LLMResponse(
            text=self.response_text,
            model=request.model,
            provider=self.name,
            usage=LLMUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        response = await self.complete(request)
        yield LLMChunk(delta=response.text)
        yield LLMChunk(delta="", done=True, usage=response.usage)

    async def aclose(self) -> None:
        return None
