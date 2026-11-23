from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from app.llm.base import (
    LLMChunk,
    LLMProvider,
    LLMProviderError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)
from app.security.secrets.base import SecretValue


class AnthropicProvider(LLMProvider):
    """Official Anthropic SDK adapter.

    Requires the optional ``anthropic`` extra:
    ``pip install -e '.[anthropic]'``.
    """

    name = "anthropic"

    def __init__(
        self,
        api_key: SecretValue,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
        thinking: str | None = "adaptive",
    ) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key.reveal(),
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._timeout = timeout_seconds
        self._thinking = thinking

    def _kwargs(self, request: LLMRequest) -> dict[str, Any]:
        system_parts: list[str] = []
        if request.system:
            system_parts.append(request.system)

        messages: list[dict[str, str]] = []
        for message in request.messages:
            if message.role == "system":
                system_parts.append(message.content)
            else:
                messages.append({"role": message.role, "content": message.content})

        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        # Current Anthropic models reject sampling parameters such as
        # temperature; use thinking/effort instead. LLMRequest.temperature is
        # kept for OpenAI-compatible providers.
        if self._thinking is not None:
            kwargs["thinking"] = {"type": self._thinking}
        return kwargs

    @staticmethod
    def _usage(response: Any) -> LLMUsage:
        usage = getattr(response, "usage", None)
        return LLMUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )

    @staticmethod
    def _text(response: Any) -> str:
        return "".join(
            str(getattr(block, "text", ""))
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        )

    @staticmethod
    def _check_refusal(response: Any, model: str) -> None:
        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMRefusalError(details={"model": model})

    def _client_for(self, request: LLMRequest) -> Any:
        return self._client.with_options(timeout=request.timeout_seconds or self._timeout)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = await self._client_for(request).messages.create(**self._kwargs(request))
        except anthropic.RateLimitError as exc:
            raise LLMProviderError(
                "provider rate limited",
                provider=self.name,
                status_code=429,
                retryable=True,
                cause=exc,
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LLMProviderError(
                f"provider returned HTTP {exc.status_code}",
                provider=self.name,
                status_code=exc.status_code,
                retryable=exc.status_code in {408, 409, 429} or exc.status_code >= 500,
                cause=exc,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError(
                "provider connection error",
                provider=self.name,
                retryable=True,
                cause=exc,
            ) from exc

        self._check_refusal(response, request.model)
        return LLMResponse(
            text=self._text(response),
            model=str(getattr(response, "model", request.model)),
            provider=self.name,
            usage=self._usage(response),
            stop_reason=getattr(response, "stop_reason", None),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        try:
            async with self._client_for(request).messages.stream(**self._kwargs(request)) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield LLMChunk(delta=text)
                final = await stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise LLMProviderError(
                "provider rate limited",
                provider=self.name,
                status_code=429,
                retryable=True,
                cause=exc,
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LLMProviderError(
                f"provider returned HTTP {exc.status_code}",
                provider=self.name,
                status_code=exc.status_code,
                retryable=exc.status_code in {408, 409, 429} or exc.status_code >= 500,
                cause=exc,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError(
                "provider connection error",
                provider=self.name,
                retryable=True,
                cause=exc,
            ) from exc

        self._check_refusal(final, request.model)
        yield LLMChunk(delta="", done=True, usage=self._usage(final))

    async def aclose(self) -> None:
        await self._client.close()
