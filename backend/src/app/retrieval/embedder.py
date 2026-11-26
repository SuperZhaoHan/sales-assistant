"""Embedder（/ɪmˈbedər/，向量化器）：把一段文字变成一个定长向量。

1.5 先用「哈希 embedder」把管线跑通：
- 确定性：同一段文字每次得到同一个向量，评测数字才可复现；
- 零依赖：不联网、不下载模型，30 条语料一次灌库毫秒级；
- 但它只做字面匹配（字符二元组），语义相近但用词不同的问句会漏。

接真模型时只换这一层：实现同一个 ``embed``，Retriever 和存储层一行都不用改。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

import httpx

from app.core.errors import ConfigurationError, ExternalServiceError
from app.security.secrets.base import SecretValue

# 只保留中文与字母数字，标点、空格、换行一律丢掉 —— 否则「，」也会被算进特征
_KEEP = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


class Embedder(Protocol):
    """可替换边界：任何实现了 ``dim`` 与 ``embed`` 的对象都能顶上来。"""

    dim: int

    def embed(self, text: str) -> tuple[float, ...]: ...

    def embed_many(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class HashingEmbedder:
    """字符 n-gram 哈希到固定维度，再归一化。"""

    # [TUTOR-JAVA] __init__ 里的默认参数是「编译期常量」，实例化时求值一次；
    # [TUTOR-JAVA] 不像 Java 的 final 字段必须在声明处赋值，Python 习惯写在签名里。
    def __init__(self, dim: int = 256) -> None:
        if dim <= 0:
            raise ValueError("dim 必须为正数")
        self.dim = dim

    def embed_many(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.embed(text) for text in texts)

    def embed(self, text: str) -> tuple[float, ...]:
        cleaned = _KEEP.sub(" ", text).strip().lower()
        if not cleaned:
            return tuple([0.0] * self.dim)

        # [TUTOR-WHY] 用二元组而不是单字：单字「保」「险」区分度太低，二元组能带上词序信息。
        grams = self._grams(cleaned)
        # 累加计数到桶里（哈希桶碰撞是允许的，256 个桶装 30 条语料绰绰有余）
        buckets = [0.0] * self.dim
        for gram in grams:
            buckets[self._bucket(gram)] += 1.0

        # [TUTOR-JAVA] 归一化 ≈ 把向量投影到单位圆上，之后相似度就是点积，不用再除模长。
        norm = sum(value * value for value in buckets) ** 0.5
        if norm == 0.0:
            return tuple(buckets)
        return tuple(value / norm for value in buckets)

    @staticmethod
    def _grams(cleaned: str) -> list[str]:
        parts = cleaned.split()
        grams: list[str] = []
        for part in parts:
            if len(part) <= 1:
                grams.append(part)
                continue
            grams.extend(part[i : i + 2] for i in range(len(part) - 1))
        return grams

    def _bucket(self, gram: str) -> int:
        # [TUTOR-WHY] 用 blake2b 而不是内置 hash()：后者每个进程加盐，重启后分数会变，
        # [TUTOR-WHY] 评测数字就不可复现了。
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dim


class OpenAICompatEmbedder:
    """OpenAI 兼容的 ``POST {base_url}/embeddings`` 适配器（硅基流动 bge-m3 走这条）。

    三个必须做的事：
    1. 批量：30 条语料一次请求，而不是 30 次 HTTP；
    2. 归一化：存储层用点积当相似度，只对单位向量等于余弦 —— 不归一化分数会偏；
    3. 维度校验：模型换版本改了维度，必须立刻报错，否则全库分数静默算错。
    """

    def __init__(
        self,
        api_key: SecretValue,
        base_url: str,
        model: str,
        dim: int,
        *,
        timeout_seconds: float = 30.0,
        batch_size: int = 32,
    ) -> None:
        if not base_url:
            raise ConfigurationError("embedding.base_url 必填（provider=openai_compat）")
        if batch_size < 1:
            raise ConfigurationError("embedding.batch_size 必须为正数")
        self.dim = dim
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._batch_size = batch_size
        self._client = httpx.Client(timeout=timeout_seconds)

    @property
    def model(self) -> str:
        """给日志/评测脚本用：真跑起来必须能说清「用的是哪把尺子」。"""
        return self._model

    def embed(self, text: str) -> tuple[float, ...]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._request(list(texts[start : start + self._batch_size])))
        return tuple(vectors)

    def _request(self, batch: list[str]) -> list[tuple[float, ...]]:
        try:
            response = self._client.post(
                f"{self._base_url}/embeddings",
                json={"model": self._model, "input": batch, "encoding_format": "float"},
                headers={"Authorization": f"Bearer {self._api_key.reveal()}"},
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                "embedding 请求失败",
                provider="embedding",
                operation="embeddings",
                retryable=True,
                cause=exc,
            ) from exc

        if response.status_code >= 400:
            raise ExternalServiceError(
                f"embedding 返回 HTTP {response.status_code}",
                provider="embedding",
                operation="embeddings",
                status_code=response.status_code,
                retryable=response.status_code in {408, 409, 429} or response.status_code >= 500,
            )

        try:
            rows = response.json()["data"]
            raw = [row["embedding"] for row in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise ExternalServiceError(
                "embedding 响应格式非法",
                provider="embedding",
                operation="embeddings",
                cause=exc,
            ) from exc

        if len(raw) != len(batch):
            raise ExternalServiceError(
                f"embedding 返回条数不符：要 {len(batch)} 条，拿到 {len(raw)} 条",
                provider="embedding",
                operation="embeddings",
            )
        return [_normalize(vector, self.dim) for vector in raw]

    def aclose(self) -> None:
        self._client.close()


def _normalize(vector: Sequence[float], dim: int) -> tuple[float, ...]:
    if len(vector) != dim:
        raise ConfigurationError(
            f"embedding 维度不符：配置 {dim}，模型返回 {len(vector)}；改配置里的 embedding.dim"
        )
    values = tuple(float(value) for value in vector)
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        return values
    return tuple(value / norm for value in values)
