"""按配置造 embedder：配置说的是「用哪种」，这里决定「怎么建」。"""

from __future__ import annotations

from app.core.config import EmbeddingConfig
from app.core.errors import ConfigurationError
from app.retrieval.embedder import Embedder, HashingEmbedder, OpenAICompatEmbedder
from app.security.secrets.base import SecretValue
from app.security.secrets.resolver import SecretResolver


def build_embedder(config: EmbeddingConfig, api_key: SecretValue | None = None) -> Embedder:
    if config.provider == "hash":
        return HashingEmbedder(dim=config.dim)
    if config.provider == "openai_compat":
        if not config.api_key_ref:
            raise ConfigurationError("embedding.api_key_ref 必填（provider=openai_compat）")
        if api_key is None:
            raise ConfigurationError(f"embedding 密钥缺失：{config.api_key_ref}")
        if not config.base_url:
            raise ConfigurationError("embedding.base_url 必填（provider=openai_compat）")
        return OpenAICompatEmbedder(
            api_key=api_key,
            base_url=config.base_url,
            model=config.model,
            dim=config.dim,
            timeout_seconds=config.timeout_seconds,
            batch_size=config.batch_size,
        )
    raise ConfigurationError(f"不支持的 embedding provider：{config.provider}")


async def build_embedder_from_settings(
    config: EmbeddingConfig,
    secrets: SecretResolver,
) -> Embedder:
    """对外唯一入口：需要密钥时先解析密钥，再交给同步构造函数。"""
    key: SecretValue | None = None
    if config.provider == "openai_compat" and config.api_key_ref:
        key = await secrets.resolve(config.api_key_ref)
    return build_embedder(config, key)
