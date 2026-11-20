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


class ChatEnvelope(BaseModel):
    ok: bool = True
    data: ChatResponse
