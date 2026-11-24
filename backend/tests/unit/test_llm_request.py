from __future__ import annotations

from app.llm.base import LLMMessage, LLMRequest, estimate_tokens


def _request(*contents: str, system: str | None = None) -> LLMRequest:
    return LLMRequest(
        messages=tuple(LLMMessage(role="user", content=content) for content in contents),
        model="test-model",
        system=system,
    )


def test_input_text_without_system_is_message_only() -> None:
    request = _request("hello")

    assert request.input_text() == "hello"


def test_input_text_includes_system_first() -> None:
    request = _request("hello", system="be concise")

    assert request.input_text() == "be concise\nhello"


def test_system_counts_toward_estimated_tokens() -> None:
    """漏算 system 会让四段模板的几百字符完全不计费，成本少一个量级。"""

    template = "四段式模板" * 100  # 400 字符，约 100 token
    with_system = _request("hi", system=template)
    without_system = _request("hi")

    assert estimate_tokens(without_system.input_text()) == 1
    assert estimate_tokens(with_system.input_text()) >= 100


def test_empty_system_is_ignored() -> None:
    request = _request("hello", system="")

    assert request.input_text() == "hello"
