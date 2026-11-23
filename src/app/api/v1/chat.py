from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_llm_gateway, get_settings
from app.api.v1.schemas import (
    ChatEnvelope,
    ChatRequest,
    ChatResponse,
    UsageResponse,
)
from app.core.config import AppConfig
from app.core.errors import AppError, InternalError
from app.llm.base import LLMMessage, LLMRequest
from app.llm.gateway import LLMGateway
from app.llm.prompts import PromptTemplate, get_template
from app.risk.models import RiskSubject

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


def _resolve_template(payload: ChatRequest) -> PromptTemplate | None:
    """按请求里的 template_name 取四段式模板；未指定则不使用模板。"""
    if not payload.template_name:
        return None
    return get_template(payload.template_name, payload.template_version)


def _build_request(
    payload: ChatRequest,
    settings: AppConfig,
    template: PromptTemplate | None = None,
) -> LLMRequest:
    system = template.render(payload.template_vars) if template else None
    metadata = dict(payload.metadata)
    if template is not None:
        # 把模板身份写进 metadata：审计日志里能还原「这条回答是哪版提示词产出的」。
        metadata["prompt_template"] = template.fingerprint()
    return LLMRequest(
        messages=tuple(
            LLMMessage(role=message.role, content=message.content) for message in payload.messages
        ),
        model=payload.model or settings.llm.model,
        system=system,
        max_tokens=payload.max_tokens or settings.llm.max_tokens,
        temperature=payload.temperature,
        metadata=metadata,
    )


def _subject(payload: ChatRequest, request: Request) -> RiskSubject:
    return RiskSubject(
        subject_id=payload.subject_id,
        tenant_id=payload.tenant_id,
        ip=request.client.host if request.client else None,
    )


@router.post("", response_model=ChatEnvelope)
async def chat(
    payload: ChatRequest,
    request: Request,
    settings: AppConfig = Depends(get_settings),
    gateway: LLMGateway = Depends(get_llm_gateway),
) -> ChatEnvelope:
    template = _resolve_template(payload)
    started = time.perf_counter()
    response = await gateway.complete(
        _build_request(payload, settings, template),
        _subject(payload, request),
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return ChatEnvelope(
        data=ChatResponse(
            text=response.text,
            model=response.model,
            provider=response.provider,
            usage=UsageResponse(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
                cost_usd=response.usage.cost_usd,
            ),
            stop_reason=response.stop_reason,
            latency_ms=latency_ms,
            prompt_template=template.fingerprint() if template else None,
        )
    )


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    settings: AppConfig = Depends(get_settings),
    gateway: LLMGateway = Depends(get_llm_gateway),
) -> StreamingResponse:
    template = _resolve_template(payload)
    llm_request = _build_request(payload, settings, template)
    subject = _subject(payload, request)
    template_fingerprint = template.fingerprint() if template else None

    async def event_stream() -> AsyncIterator[str]:
        started = time.perf_counter()
        try:
            async for chunk in gateway.stream(llm_request, subject):
                if chunk.delta:
                    yield (
                        "data: " + json.dumps({"delta": chunk.delta}, ensure_ascii=False) + "\n\n"
                    )
                if chunk.done and chunk.usage is not None:
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "done": True,
                                "usage": {
                                    "input_tokens": chunk.usage.input_tokens,
                                    "output_tokens": chunk.usage.output_tokens,
                                    "total_tokens": chunk.usage.total_tokens,
                                    "cost_usd": chunk.usage.cost_usd,
                                },
                                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                                "prompt_template": template_fingerprint,
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
            yield "data: [DONE]\n\n"
        except AppError as exc:
            yield (
                "data: " + json.dumps({"error": exc.to_public_dict()}, ensure_ascii=False) + "\n\n"
            )
        except Exception:
            logger.exception("chat_stream_failed")
            yield (
                "data: "
                + json.dumps({"error": InternalError().to_public_dict()}, ensure_ascii=False)
                + "\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
