from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.llm.base import (
    LLMChunk,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    estimate_tokens,
)


class FakeLLMProvider(LLMProvider):
    """Deterministic provider for local development and tests."""

    name = "fake"

    def __init__(self, model: str = "fake-model", delay_seconds: float = 0.0) -> None:
        self._model = model
        self._delay_seconds = delay_seconds

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        last_user = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        text = f"[fake:{request.model}] {last_user}".strip()
        usage = LLMUsage(
            input_tokens=estimate_tokens(request.input_text()),
            output_tokens=estimate_tokens(text),
        )
        return LLMResponse(
            text=text,
            model=request.model or self._model,
            provider=self.name,
            usage=usage,
            stop_reason="end_turn",
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        response = await self.complete(request)
        for word in response.text.split():
            yield LLMChunk(delta=f"{word} ")
            if self._delay_seconds:
                await asyncio.sleep(0)
        yield LLMChunk(delta="", done=True, usage=response.usage)

    async def aclose(self) -> None:
        return None
