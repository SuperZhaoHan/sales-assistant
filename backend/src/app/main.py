from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.chat import router as chat_router
from app.api.v1.demo import router as demo_router
from app.core.config import AppConfig, load_config
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.domains import load_domain
from app.llm.factory import build_llm_gateway
from app.observability.health import router as health_router
from app.observability.metrics import router as metrics_router
from app.observability.tracing import setup_tracing
from app.risk.engine import build_risk_engine
from app.security.secrets.factory import build_secret_resolver, validate_required_secrets

logger = structlog.get_logger(__name__)


def create_app(settings: AppConfig | None = None) -> FastAPI:
    settings = settings or load_config()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # 第一个动作就是加载行业包：配置写错就死在这里，别等到第一个请求才炸
        domain = load_domain(settings.app.domain)
        secrets = build_secret_resolver(settings)
        await validate_required_secrets(settings, secrets)
        risk = build_risk_engine(settings)
        llm = await build_llm_gateway(settings, secrets, risk)

        application.state.domain = domain
        application.state.secrets = secrets
        application.state.risk = risk
        application.state.llm = llm
        application.state.ready = True

        logger.info(
            "app_started",
            env=settings.env,
            domain=domain.name,
            templates=len(domain.templates),
            rules=domain.rule_count,
            provider=settings.llm.provider,
            model=settings.llm.model,
            risk_enabled=settings.risk.enabled,
        )
        try:
            yield
        finally:
            application.state.ready = False
            await llm.aclose()
            await risk.aclose()
            logger.info("app_stopped", env=settings.env)

    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        docs_url="/docs" if settings.server.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.server.docs_enabled else None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.ready = False
    app.state.domain = None
    app.state.secrets = None
    app.state.risk = None
    app.state.llm = None

    if settings.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware, settings=settings)

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(chat_router, prefix="/v1")
    if settings.env in {"local", "test"}:
        app.include_router(demo_router, prefix="/v1")

    if settings.observability.metrics_enabled:
        app.include_router(metrics_router)

    setup_tracing(settings, app)
    return app
