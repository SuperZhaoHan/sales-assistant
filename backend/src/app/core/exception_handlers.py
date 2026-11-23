from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.context import get_request_id
from app.core.error_codes import ErrorCode
from app.core.errors import AppError, InternalError, ValidationError

logger = structlog.get_logger(__name__)

_HTTP_CODE_MAP: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.AUTHENTICATION_ERROR,
    403: ErrorCode.PERMISSION_DENIED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.VALIDATION_ERROR,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMIT,
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or get_request_id()


def _envelope(error: AppError, request_id: str | None) -> dict[str, Any]:
    payload = error.to_public_dict()
    payload["request_id"] = request_id
    return {"ok": False, "error": payload}


def _json_response(error: AppError, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }
    if request_id:
        headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=error.http_status,
        content=_envelope(error, request_id),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        log_method = logger.error if exc.http_status >= 500 else logger.warning
        log_method(
            "app_error",
            code=exc.code,
            status_code=exc.http_status,
            details=exc.details,
            retryable=exc.retryable,
            exc_info=exc if exc.http_status >= 500 else None,
        )
        return _json_response(exc, request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        fields = [
            {
                "loc": ".".join(str(part) for part in error.get("loc", ())),
                "msg": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        error = ValidationError(details={"fields": fields})
        logger.warning("request_validation_failed", fields=fields)
        return _json_response(error, request)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        code = _HTTP_CODE_MAP.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        error = AppError(code=code, http_status=exc.status_code)
        if exc.status_code >= 500:
            logger.error(
                "http_exception",
                status_code=exc.status_code,
                detail_type=type(exc.detail).__name__,
                exc_info=exc,
            )
        else:
            logger.warning("http_exception", status_code=exc.status_code, detail=str(exc.detail))
        return _json_response(error, request)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return _json_response(InternalError(), request)
