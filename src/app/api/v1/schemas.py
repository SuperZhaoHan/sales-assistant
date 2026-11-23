from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequest(BaseModel):
    """Public chat request.

    ``subject_id`` is a placeholder for the authenticated principal. In a real
    deployment, derive it from the auth layer instead of trusting the body.
    """

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=128)
    subject_id: str = Field(default="anonymous", min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    max_tokens: int | None = Field(default=None, gt=0, le=128_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 四段式提示词模板：给了 template_name 就由后端渲染 system prompt，
    # 调用方不必自己拼提示词，也就不会出现「各写一个版本、改不动」的问题。
    template_name: str | None = Field(default=None, max_length=64)
    template_version: str | None = Field(default=None, max_length=16)
    template_vars: dict[str, str] = Field(default_factory=dict)


class UsageResponse(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float


class ChatResponse(BaseModel):
    text: str
    model: str
    provider: str
    usage: UsageResponse
    stop_reason: str | None = None
    # 端到端耗时（毫秒），与 usage.cost_usd 一起构成「每次调用的耗时与费用」记录。
    latency_ms: float
    prompt_template: dict[str, str] | None = None


class ChatEnvelope(BaseModel):
    ok: bool = True
    data: ChatResponse
