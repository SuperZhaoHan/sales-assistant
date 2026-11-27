"""按配置造 embedder：配置说的是「用哪种」，这里决定「怎么建」。"""

from __future__ import annotations

from app.core.config import EmbeddingConfig, RetrievalConfig
from app.core.errors import ConfigurationError
from app.retrieval.embedder import Embedder, HashingEmbedder, OpenAICompatEmbedder
from app.retrieval.store import MemoryVectorStore, PgVectorStore, VectorStore
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


def build_vector_store(config: RetrievalConfig, *, dim: int) -> VectorStore:
    """存储后端：memory 直接给内存表，pgvector 才连库。

    ``dim`` 从 embedding 配置来：表和向量的维度必须同一个来源，
    否则「配置 1024、模型返 1536」这种错会在入库时才炸（那时数据已经脏了）。
    """
    if config.backend == "memory":
        return MemoryVectorStore()
    if config.backend == "pgvector":
        if not config.dsn:
            raise ConfigurationError("retrieval.backend=pgvector 时必须配 retrieval.dsn")
        # 只读部署跳过建表语句：库由 scripts/build_index.py 负责，启动只连不建
        return PgVectorStore(
            config.dsn, dim=dim, table=config.table, prepare=config.indexing == "sync"
        )
    raise ConfigurationError(f"不支持的检索后端：{config.backend}")
