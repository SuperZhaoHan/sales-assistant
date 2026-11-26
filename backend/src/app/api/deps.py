from __future__ import annotations

from typing import cast

from fastapi import Request

from app.core.config import AppConfig
from app.core.errors import ConfigurationError
from app.domains.registry import DomainPack
from app.llm.gateway import LLMGateway
from app.risk.engine import RiskEngine
from app.security.secrets.resolver import SecretResolver
from app.store.sqlite_store import CustomerStore


def get_settings(request: Request) -> AppConfig:
    return cast(AppConfig, request.app.state.settings)


def get_llm_gateway(request: Request) -> LLMGateway:
    gateway = getattr(request.app.state, "llm", None)
    if gateway is None:
        raise ConfigurationError("LLM gateway is not initialized")
    return cast(LLMGateway, gateway)


def get_risk_engine(request: Request) -> RiskEngine:
    engine = getattr(request.app.state, "risk", None)
    if engine is None:
        raise ConfigurationError("risk engine is not initialized")
    return cast(RiskEngine, engine)


def get_secret_resolver(request: Request) -> SecretResolver:
    resolver = getattr(request.app.state, "secrets", None)
    if resolver is None:
        raise ConfigurationError("secret resolver is not initialized")
    return cast(SecretResolver, resolver)


def get_store(request: Request) -> CustomerStore:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise ConfigurationError("customer store is not initialized")
    return cast(CustomerStore, store)


def get_domain(request: Request) -> DomainPack:
    domain = getattr(request.app.state, "domain", None)
    if domain is None:
        raise ConfigurationError("domain pack is not initialized")
    return cast(DomainPack, domain)
