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


class SummaryRequest(BaseModel):
    """按客户名生成续保摘要。

    与 ``/chat`` 的区别：调用方不必自己拼提示词，也不传历史消息——
    资料由后端查库得来，模型只看后端给的事实。
    """

    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(min_length=1, max_length=40)
    subject_id: str = Field(default="anonymous", min_length=1, max_length=128)
    tenant_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    max_tokens: int | None = Field(default=None, gt=0, le=128_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class CitationResponse(BaseModel):
    """一条引用：编号 + 反查到的原文。

    ``resolved=False`` 表示这个编号后端从没发过 —— 模型编的，或者写错了。
    """

    label: str
    kind: Literal["fact", "knowledge", "unknown"]
    ref_id: str
    source: str
    resolved: bool


class SummaryResponse(BaseModel):
    text: str
    model: str
    provider: str
    # 查到了这个客户吗；查不到时 text 是固定话术，llm_called=False、token 全 0
    found: bool
    customer_name: str
    # 本次可用来校验摘要的事实清单（到期日/状态/最近互动），供上游做引用反查
    facts: list[str]
    # 摘要里出现的引用及其反查结果
    citations: list[CitationResponse]
    # 可追溯率 = 能查回原文的引用 / 总引用；一条引用都没有时为 null，不算 100%
    traceable_rate: float | None = None
    usage: UsageResponse
    stop_reason: str | None = None
    latency_ms: float
    prompt_template: dict[str, str] | None = None
    llm_called: bool


class SummaryEnvelope(BaseModel):
    ok: bool = True
    data: SummaryResponse
