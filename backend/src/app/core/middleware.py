from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import AppConfig
from app.observability.metrics import observe_http_request

logger = structlog.get_logger(__name__)
_NOISY_PATHS = {"/health/live", "/health/ready", "/metrics"}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, structured log context and access log."""

    def __init__(self, app: Callable[..., Awaitable[None]], settings: AppConfig) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        header = self._settings.server.request_id_header
        request_id = request.headers.get(header) or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            observe_http_request(request.method, request.url.path, 500, duration)
            logger.exception(
                "request_failed",
                duration_ms=round(duration * 1000, 2),
            )
            raise

        duration = time.perf_counter() - started
        response.headers[header] = request_id
        observe_http_request(request.method, request.url.path, response.status_code, duration)

        if self._settings.logging.access_log and request.url.path not in _NOISY_PATHS:
            logger.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )

        structlog.contextvars.clear_contextvars()
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply a small, safe baseline of browser security headers."""

    def __init__(self, app: Callable[..., Awaitable[None]], settings: AppConfig) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if self._settings.env == "prod":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
