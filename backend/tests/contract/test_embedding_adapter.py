from __future__ import annotations

import json
import math

import httpx
import pytest
import respx

from app.core.config import EmbeddingConfig
from app.core.errors import ConfigurationError, ExternalServiceError
from app.retrieval.embedder import OpenAICompatEmbedder
from app.retrieval.factory import build_embedder
from app.security.secrets.base import SecretValue

BASE_URL = "https://api.example.com/v1"
DIM = 4


def make_embedder(batch_size: int = 2, dim: int = DIM) -> OpenAICompatEmbedder:
    return OpenAICompatEmbedder(
        api_key=SecretValue("sk-embed"),
        base_url=BASE_URL,
        model="BAAI/bge-m3",
        dim=dim,
        batch_size=batch_size,
    )


def _payload(vectors: list[list[float]]) -> dict:
    return {"object": "list", "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]}


async def test_embedding_batches_multiple_texts() -> None:
    """3 段文字、batch=2 → 必须发两次请求，而不是三次或一次。"""
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/embeddings").mock(
            return_value=httpx.Response(200, json=_payload([[1, 0, 0, 0], [0, 2, 0, 0]]))
        )
        embedder = make_embedder(batch_size=2)
        # 第一批发 2 条，剩 1 条会被截断成 1 条：用 side_effect 返回两种条数
        route.side_effect = [
            httpx.Response(200, json=_payload([[1, 0, 0, 0], [0, 2, 0, 0]])),
            httpx.Response(200, json=_payload([[0, 0, 3, 0]])),
        ]
        vectors = embedder.embed_many(["a", "b", "c"])
        embedder.aclose()

    assert len(vectors) == 3
    assert route.call_count == 2

    first = json.loads(route.calls[0].request.content)
    assert first["model"] == "BAAI/bge-m3"
    assert first["input"] == ["a", "b"]


async def test_embedding_normalizes_to_unit_length() -> None:
    """存储层用点积当余弦，向量必须是单位长度，否则分数不可比。"""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/embeddings").mock(
            return_value=httpx.Response(200, json=_payload([[3.0, 4.0, 0.0, 0.0]]))
        )
        embedder = make_embedder()
        vector = embedder.embed("重疾险等待期多久")
        embedder.aclose()

    assert math.isclose(sum(value * value for value in vector) ** 0.5, 1.0, rel_tol=1e-9)
    assert vector[0] == pytest.approx(0.6)
    assert vector[1] == pytest.approx(0.8)


async def test_embedding_dim_mismatch_raises() -> None:
    """配置写 4 维、模型返回 3 维：必须报错，不能静默算错点积。"""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/embeddings").mock(
            return_value=httpx.Response(200, json=_payload([[1.0, 0.0, 0.0]]))
        )
        embedder = make_embedder()
        with pytest.raises(ConfigurationError):
            embedder.embed("x")
        embedder.aclose()


async def test_embedding_count_mismatch_raises() -> None:
    """要 2 条只回 1 条：说明服务端丢数据了，直接失败而不是错位配对。"""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/embeddings").mock(
            return_value=httpx.Response(200, json=_payload([[1.0, 0.0, 0.0, 0.0]]))
        )
        embedder = make_embedder()
        with pytest.raises(ExternalServiceError):
            embedder.embed_many(["a", "b"])
        embedder.aclose()


async def test_embedding_http_error_raises_retryable() -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/embeddings").mock(return_value=httpx.Response(429))
        embedder = make_embedder()
        with pytest.raises(ExternalServiceError):
            embedder.embed("x")
        embedder.aclose()


def test_factory_returns_hash_embedder_by_default() -> None:
    embedder = build_embedder(EmbeddingConfig())
    assert embedder.dim == 256


def test_factory_requires_key_for_openai_compat() -> None:
    """配了 openai_compat 却没有密钥：启动期就报错，不要等到第一次检索才炸。"""
    config = EmbeddingConfig(
        provider="openai_compat",
        model="BAAI/bge-m3",
        base_url=BASE_URL,
        api_key_ref="secret://embedding/api_key",
        dim=1024,
    )
    with pytest.raises(ConfigurationError):
        build_embedder(config)
