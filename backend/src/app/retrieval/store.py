"""向量存储：1.5 先用内存实现，接口留成可替换的（生产实现是 pgvector）。

为什么内存先上：
- 30 条语料，纯 Python 点积一次 20 µs 量级，比连数据库还快；
- 单测不需要容器，CI 不用等 Docker；
- 真实现（``PgVectorStore``）只影响 ``add`` / ``search`` 两个方法。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Chunk:
    """一个知识块。``id`` 是引用编号的锚点，改了就断链。"""

    id: str
    title: str
    text: str
    tags: tuple[str, ...] = ()
    tenant_id: str = "default"


@dataclass(frozen=True, slots=True)
class Hit:
    chunk: Chunk
    score: float


class MemoryVectorStore:
    """把向量和块一起放在列表里，检索时全表扫一遍。"""

    def __init__(self) -> None:
        self._vectors: list[tuple[float, ...]] = []
        self._chunks: list[Chunk] = []

    def __len__(self) -> int:
        return len(self._chunks)

    def add(self, chunk: Chunk, vector: Sequence[float]) -> None:
        # 维度必须全库一致：混进来一个 384 维的真模型向量，点积会静默算错
        if self._vectors and len(vector) != len(self._vectors[0]):
            raise ValueError(f"向量维度与已有索引不一致：{len(vector)} vs {len(self._vectors[0])}")
        self._chunks.append(chunk)
        self._vectors.append(tuple(vector))

    def add_many(self, items: Iterable[tuple[Chunk, Sequence[float]]]) -> None:
        for chunk, vector in items:
            self.add(chunk, vector)

    def search(
        self, vector: Sequence[float], *, top_k: int = 3, tenant_id: str = "default"
    ) -> tuple[Hit, ...]:
        if top_k <= 0:
            raise ValueError("top_k 必须为正数")
        scored: list[Hit] = []
        for chunk, stored in zip(self._chunks, self._vectors, strict=True):
            # [TUTOR-WHY] 租户过滤放在打分之前：先算完再筛，等于把别家的内容算进了候选池，
            # [TUTOR-WHY] 一旦有人把 top_k 当断点用，就会漏。
            if chunk.tenant_id != tenant_id:
                continue
            score = _dot(vector, stored)
            scored.append(Hit(chunk=chunk, score=score))
        # [TUTOR-JAVA] sorted(..., key=...) ≈ 传入 Comparator；reverse=True 就是从大到小。
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return tuple(scored[:top_k])


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))
