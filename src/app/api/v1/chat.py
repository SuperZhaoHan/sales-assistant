from __future__ import annotations

import json
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
from app.risk.models import RiskSubject

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


def _build_request(payload: ChatRequest, settings: AppConfig) -> LLMRequest:
    return LLMRequest(
        messages=tuple(
            LLMMessage(role=message.role, content=message.content) for message in payload.messages
        ),
        model=payload.model or settings.llm.model,
        max_tokens=payload.max_tokens or settings.llm.max_tokens,
        temperature=payload.temperature,
        metadata=payload.metadata,
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
    response = await gateway.complete(_build_request(payload, settings), _subject(payload, request))
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
        )
    )


@router.post("/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    settings: AppConfig = Depends(get_settings),
    gateway: LLMGateway = Depends(get_llm_gateway),
) -> StreamingResponse:
    llm_request = _build_request(payload, settings)
    subject = _subject(payload, request)

    async def event_stream() -> AsyncIterator[str]:
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
