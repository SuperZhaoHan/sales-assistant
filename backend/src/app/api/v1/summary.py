from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends, Request

from app.agent.brief import build_brief, not_found_text
from app.agent.citation import build_catalog, resolve_citations
from app.agent.summary import build_summary_request
from app.api.deps import get_domain, get_llm_gateway, get_settings, get_store
from app.api.v1.schemas import (
    CitationResponse,
    SummaryEnvelope,
    SummaryRequest,
    SummaryResponse,
    UsageResponse,
)
from app.core.config import AppConfig
from app.domains.registry import DomainPack
from app.llm.gateway import LLMGateway
from app.risk.models import RiskSubject
from app.store.sqlite_store import CustomerStore

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/summary", tags=["summary"])


@router.post("", response_model=SummaryEnvelope)
async def create_summary(
    payload: SummaryRequest,
    request: Request,
    settings: AppConfig = Depends(get_settings),
    gateway: LLMGateway = Depends(get_llm_gateway),
    store: CustomerStore = Depends(get_store),
    domain: DomainPack = Depends(get_domain),
) -> SummaryEnvelope:
    """按客户名生成续保摘要：查档 → 填模板 → 调模型。

    查不到客户时不调模型：直接回固定话术，token 花 0。
    """
    started = time.perf_counter()
    brief = build_brief(store, payload.customer_name)
    llm_request = build_summary_request(
        domain,
        brief,
        model=payload.model or settings.llm.model,
        max_tokens=payload.max_tokens or settings.llm.max_tokens,
        temperature=payload.temperature,
    )

    if llm_request is None:
        # [TUTOR-WHY] 查无此人就走这条路：不打模型，usage 全 0，上游一眼能看出来
        logger.info("summary_customer_not_found", customer_name=payload.customer_name)
        return SummaryEnvelope(
            data=SummaryResponse(
                text=not_found_text(payload.customer_name),
                model="",
                provider="none",
                found=False,
                customer_name=payload.customer_name,
                facts=[],
                citations=[],
                traceable_rate=None,
                usage=UsageResponse(input_tokens=0, output_tokens=0, total_tokens=0, cost_usd=0.0),
                stop_reason=None,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                prompt_template=None,
                llm_called=False,
            )
        )

    subject = RiskSubject(
        subject_id=payload.subject_id,
        tenant_id=payload.tenant_id,
        ip=request.client.host if request.client else None,
    )
    response = await gateway.complete(llm_request, subject)

    # [TUTOR-WHY] 反查放在模型返回之后、响应组装之前：引用判定只依赖 facts，不依赖模型自述
    catalog = build_catalog(brief.facts)
    citations, traceable_rate = resolve_citations(response.text, catalog)

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "summary_generated",
        customer_name=payload.customer_name,
        total_tokens=response.usage.total_tokens,
        cost_usd=response.usage.cost_usd,
        latency_ms=latency_ms,
        citations=len(citations),
        traceable_rate=traceable_rate,
    )
    return SummaryEnvelope(
        data=SummaryResponse(
            text=response.text,
            model=response.model,
            provider=response.provider,
            found=True,
            customer_name=brief.customer_name,
            facts=list(brief.facts),
            citations=[
                CitationResponse(
                    label=citation.label,
                    kind=citation.kind,
                    ref_id=citation.ref_id,
                    source=citation.source,
                    resolved=citation.resolved,
                )
                for citation in citations
            ],
            traceable_rate=traceable_rate,
            usage=UsageResponse(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
                cost_usd=response.usage.cost_usd,
            ),
            stop_reason=response.stop_reason,
            latency_ms=latency_ms,
            prompt_template=domain.entry.fingerprint(),
            llm_called=True,
        )
    )
