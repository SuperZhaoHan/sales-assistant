from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from app.llm.base import LLMMessage, LLMRefusalError, LLMRequest
from app.llm.providers.anthropic import AnthropicProvider
from app.security.secrets.base import SecretValue

pytestmark = pytest.mark.llm


def make_provider() -> AnthropicProvider:
    return AnthropicProvider(
        api_key=SecretValue("sk-test"),
        timeout_seconds=5,
        thinking="adaptive",
    )


def test_request_kwargs_omit_temperature() -> None:
    provider = make_provider()
    request = LLMRequest(
        messages=(LLMMessage(role="user", content="hi"),),
        model="claude-opus-5-5",
        system="be concise",
        max_tokens=100,
        temperature=0.5,
    )

    kwargs = provider._kwargs(request)

    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["max_tokens"] == 100
    assert kwargs["system"] == "be concise"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert "temperature" not in kwargs


def test_text_and_usage_extraction() -> None:
    provider = make_provider()
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="hello"),
            SimpleNamespace(type="thinking", text=""),
        ],
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
        stop_reason="end_turn",
    )

    assert provider._text(response) == "hello"
    usage = provider._usage(response)
    assert usage.input_tokens == 3
    assert usage.output_tokens == 2


def test_refusal_is_detected() -> None:
    provider = make_provider()

    with pytest.raises(LLMRefusalError):
        provider._check_refusal(
            SimpleNamespace(stop_reason="refusal"),
            "claude-opus-5-5",
        )
