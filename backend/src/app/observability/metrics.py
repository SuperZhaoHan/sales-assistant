from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

router = APIRouter()

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
LLM_REQUESTS_TOTAL = Counter(
    "llm_requests_total",
    "Total LLM requests.",
    ["provider", "model", "status"],
)
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "LLM token usage.",
    ["provider", "model", "type"],
)
LLM_COST_USD_TOTAL = Counter(
    "llm_cost_usd_total",
    "Estimated LLM cost in USD.",
    ["provider", "model"],
)
RISK_DECISIONS_TOTAL = Counter(
    "risk_decisions_total",
    "Risk engine decisions.",
    ["decision", "rule"],
)
CIRCUIT_BREAKER_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state: 0 closed, 1 half-open, 2 open.",
    ["provider"],
)


def _normalized_path(path: str) -> str:
    """Keep metric cardinality bounded.

    For a larger service, replace this with the matched route template.
    """

    if path.startswith("/v1/chat"):
        return "/v1/chat"
    return path


def observe_http_request(method: str, path: str, status_code: int, duration: float) -> None:
    normalized = _normalized_path(path)
    HTTP_REQUESTS_TOTAL.labels(method=method, path=normalized, status=str(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=normalized).observe(duration)


def record_llm_request(provider: str, model: str, status: str) -> None:
    LLM_REQUESTS_TOTAL.labels(provider=provider, model=model, status=status).inc()


def record_llm_usage(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    LLM_TOKENS_TOTAL.labels(provider=provider, model=model, type="input").inc(input_tokens)
    LLM_TOKENS_TOTAL.labels(provider=provider, model=model, type="output").inc(output_tokens)
    LLM_COST_USD_TOTAL.labels(provider=provider, model=model).inc(cost_usd)


def record_risk_decision(decision: str, rule: str) -> None:
    RISK_DECISIONS_TOTAL.labels(decision=decision, rule=rule).inc()


def set_circuit_state(provider: str, state: int) -> None:
    CIRCUIT_BREAKER_STATE.labels(provider=provider).set(state)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
