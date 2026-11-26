from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import ConfigurationError
from app.llm.base import (
    LLMChunk,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    estimate_tokens,
)
from app.security.secrets.base import SecretValue


def _extract_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


def _usage_from_payload(payload: dict[str, Any]) -> LLMUsage:
    usage = payload.get("usage") or {}
    return LLMUsage(
        input_tokens=int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0),
        output_tokens=int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
    )


class OpenAICompatProvider(LLMProvider):
    """OpenAI-compatible Chat Completions adapter.

    Works with vendors exposing ``POST {base_url}/chat/completions``. The API key
    is held as a SecretValue and only revealed in the Authorization header.
    """

    name = "openai_compat"

    def __init__(
        self,
        api_key: SecretValue,
        base_url: str,
        timeout_seconds: float = 60.0,
        thinking: str | None = None,
    ) -> None:
        if not base_url:
            raise ConfigurationError("llm.base_url is required for the openai_compat provider")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        # DeepSeek 等服务默认开思考模式，且思考 token 也算在 max_tokens 里；
        # 摘要这种"格式严格、要省 token"的任务必须显式关掉。
        self._thinking = thinking
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key.reveal()}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(
            {"role": message.role, "content": message.content} for message in request.messages
        )
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if self._thinking == "disabled":
            payload["thinking"] = {"type": "disabled"}
        if stream:
            payload["stream"] = True
        return payload

    async def complete(self, request: LLMRequest) -> LLMResponse:
        url = f"{self._base_url}/chat/completions"
        try:
            response = await self._client.post(
                url,
                json=self._payload(request, stream=False),
                headers=self._headers(),
                timeout=request.timeout_seconds or self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                "provider timeout",
                provider=self.name,
                retryable=True,
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                "provider connection error",
                provider=self.name,
                retryable=True,
                cause=exc,
            ) from exc

        if response.status_code >= 400:
            raise LLMProviderError(
                f"provider returned HTTP {response.status_code}",
                provider=self.name,
                status_code=response.status_code,
                retryable=response.status_code in {408, 409, 429} or response.status_code >= 500,
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            text = _extract_content(choice.get("message", {}).get("content"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                "invalid provider response",
                provider=self.name,
                retryable=False,
                cause=exc,
            ) from exc

        return LLMResponse(
            text=text,
            model=str(data.get("model", request.model)),
            provider=self.name,
            usage=_usage_from_payload(data),
            stop_reason=choice.get("finish_reason"),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        url = f"{self._base_url}/chat/completions"
        usage = LLMUsage()
        collected: list[str] = []
        try:
            async with self._client.stream(
                "POST",
                url,
                json=self._payload(request, stream=True),
                headers=self._headers(),
                timeout=request.timeout_seconds or self._timeout,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise LLMProviderError(
                        f"provider returned HTTP {response.status_code}",
                        provider=self.name,
                        status_code=response.status_code,
                        retryable=(
                            response.status_code in {408, 409, 429} or response.status_code >= 500
                        ),
                    )
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        payload = json.loads(raw)
                        choice = payload["choices"][0]
                        delta = _extract_content(choice.get("delta", {}).get("content"))
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise LLMProviderError(
                            "invalid streaming response",
                            provider=self.name,
                            retryable=False,
                            cause=exc,
                        ) from exc
                    if delta:
                        collected.append(delta)
                        yield LLMChunk(delta=delta)
                    if payload.get("usage"):
                        usage = _usage_from_payload(payload)
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                "provider timeout",
                provider=self.name,
                retryable=True,
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                "provider connection error",
                provider=self.name,
                retryable=True,
                cause=exc,
            ) from exc

        if usage.total_tokens == 0:
            usage = LLMUsage(
                input_tokens=estimate_tokens(request.input_text()),
                output_tokens=estimate_tokens("".join(collected)),
            )
        yield LLMChunk(delta="", done=True, usage=usage)

    async def aclose(self) -> None:
        await self._client.aclose()
