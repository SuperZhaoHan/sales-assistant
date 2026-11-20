from __future__ import annotations

import json

import httpx
import respx

from app.llm.base import LLMMessage, LLMRequest
from app.llm.openai_compat import OpenAICompatProvider
from app.security.secrets.base import SecretValue

BASE_URL = "https://api.example.com/v1"


def make_request() -> LLMRequest:
    return LLMRequest(
        messages=(LLMMessage(role="user", content="hello"),),
        model="test-model",
        system="be concise",
        max_tokens=32,
    )


async def test_openai_compat_complete_contract() -> None:
    response_payload = {
        "id": "chatcmpl-1",
        "model": "test-model",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hello back"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }

    with respx.mock(base_url=BASE_URL) as respx_mock:
        route = respx_mock.post("/chat/completions").mock(
            return_value=httpx.Response(200, json=response_payload)
        )
        provider = OpenAICompatProvider(
            api_key=SecretValue("sk-test"),
            base_url=BASE_URL,
        )

        response = await provider.complete(make_request())
        await provider.aclose()

    assert response.text == "hello back"
    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens == 2
    assert route.called

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer sk-test"
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    assert payload["max_tokens"] == 32
    assert payload["messages"] == [
        {"role": "system", "content": "be concise"},
        {"role": "user", "content": "hello"},
    ]


async def test_openai_compat_stream_contract() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    with respx.mock(base_url=BASE_URL) as respx_mock:
        respx_mock.post("/chat/completions").mock(
            return_value=httpx.Response(200, content=body.encode("utf-8"))
        )
        provider = OpenAICompatProvider(
            api_key=SecretValue("sk-test"),
            base_url=BASE_URL,
        )

        chunks = [chunk async for chunk in provider.stream(make_request())]
        await provider.aclose()

    assert "".join(chunk.delta for chunk in chunks) == "hello"
    assert chunks[-1].done is True
    assert chunks[-1].usage is not None
