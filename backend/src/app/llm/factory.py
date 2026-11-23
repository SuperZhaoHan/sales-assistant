from __future__ import annotations

from app.core.config import AppConfig
from app.core.errors import ConfigurationError
from app.llm.base import LLMProvider
from app.llm.cost import CostCalculator
from app.llm.fake import FakeLLMProvider
from app.llm.gateway import LLMGateway
from app.risk.audit import AuditLogger
from app.risk.circuit_breaker import CircuitBreakerRegistry
from app.risk.engine import RiskEngine
from app.security.secrets.resolver import SecretResolver


async def build_llm_provider(settings: AppConfig, secrets: SecretResolver) -> LLMProvider:
    config = settings.llm

    if config.provider == "fake":
        return FakeLLMProvider(model=config.model)

    if config.provider == "anthropic":
        if not config.api_key_ref:
            raise ConfigurationError("llm.api_key_ref is required for the anthropic provider")
        try:
            from app.llm.providers.anthropic import AnthropicProvider
        except ImportError as exc:
            raise ConfigurationError(
                "anthropic is not installed; run pip install -e '.[anthropic]'"
            ) from exc
        api_key = await secrets.resolve(config.api_key_ref)
        return AnthropicProvider(
            api_key=api_key,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            thinking=config.thinking,
        )

    if config.provider == "openai_compat":
        if not config.base_url:
            raise ConfigurationError("llm.base_url is required for the openai_compat provider")
        if not config.api_key_ref:
            raise ConfigurationError("llm.api_key_ref is required for the openai_compat provider")
        from app.llm.openai_compat import OpenAICompatProvider

        api_key = await secrets.resolve(config.api_key_ref)
        return OpenAICompatProvider(
            api_key=api_key,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
        )

    raise ConfigurationError(f"unsupported llm provider: {config.provider}")


async def build_llm_gateway(
    settings: AppConfig,
    secrets: SecretResolver,
    risk_engine: RiskEngine,
) -> LLMGateway:
    provider = await build_llm_provider(settings, secrets)
    breakers = CircuitBreakerRegistry(
        failure_threshold=settings.llm.circuit_failure_threshold,
        recovery_timeout=settings.llm.circuit_recovery_seconds,
    )
    calculator = CostCalculator(
        default_input=settings.llm.cost_per_1m_input,
        default_output=settings.llm.cost_per_1m_output,
    )
    return LLMGateway(
        provider=provider,
        config=settings.llm,
        risk_engine=risk_engine,
        breakers=breakers,
        calculator=calculator,
        audit=AuditLogger(),
    )
