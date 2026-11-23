from __future__ import annotations

from app.llm.base import LLMMessage, LLMRequest


def make_llm_request(
    content: str = "hello",
    *,
    model: str = "test-model",
    max_tokens: int = 100,
) -> LLMRequest:
    return LLMRequest(
        messages=(LLMMessage(role="user", content=content),),
        model=model,
        max_tokens=max_tokens,
    )
