from __future__ import annotations

import os

import structlog

from app.core.config import AppConfig
from app.core.errors import ConfigurationError
from app.security.secrets.base import SecretNotFoundError, SecretProvider, SecretRef
from app.security.secrets.resolver import SecretResolver

logger = structlog.get_logger(__name__)


def build_secret_provider(settings: AppConfig) -> SecretProvider:
    config = settings.secrets

    if config.provider == "env":
        from app.security.secrets.env import EnvSecretProvider

        return EnvSecretProvider(prefix=config.env_prefix)

    if config.provider == "file":
        from app.security.secrets.file import FileSecretProvider

        return FileSecretProvider(config.file_dir)

    if config.provider == "vault":
        from app.security.secrets.vault import VaultSecretProvider

        token = os.getenv(config.vault_token_env)
        if not token:
            raise ConfigurationError(
                f"Vault token environment variable {config.vault_token_env} is not set"
            )
        return VaultSecretProvider(
            url=config.vault_url,
            token=token,
            mount=config.vault_mount,
        )

    if config.provider == "aws":
        from app.security.secrets.aws import AWSSecretsManagerProvider

        return AWSSecretsManagerProvider(region=config.aws_region)

    raise ConfigurationError(f"unsupported secret provider: {config.provider}")


def build_secret_resolver(settings: AppConfig) -> SecretResolver:
    return SecretResolver(
        provider=build_secret_provider(settings),
        cache_ttl_seconds=settings.secrets.cache_ttl_seconds,
    )


async def validate_required_secrets(settings: AppConfig, resolver: SecretResolver) -> None:
    """Fail fast outside local/test when a required secret is missing."""

    required = list(settings.secrets.required)
    if settings.llm.provider != "fake" and settings.llm.api_key_ref:
        try:
            api_ref = SecretRef.parse(settings.llm.api_key_ref)
            if api_ref.path not in required:
                required.append(api_ref.path)
        except ValueError:
            logger.warning(
                "invalid_llm_api_key_ref",
                provider=settings.llm.provider,
            )

    missing: list[str] = []
    for name in required:
        try:
            await resolver.resolve(f"secret://{name}")
        except SecretNotFoundError:
            missing.append(name)

    if not missing:
        return

    logger.error(
        "missing_required_secrets",
        missing=missing,
        env=settings.env,
        provider=settings.secrets.provider,
    )
    if settings.env in {"staging", "prod"}:
        raise ConfigurationError("必需配置或密钥缺失")
    logger.warning("continuing_with_missing_secrets", env=settings.env)
