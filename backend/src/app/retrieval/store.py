"""向量存储：1.5 先用内存实现，接口留成可替换的（生产实现是 pgvector）。

为什么内存先上：
- 30 条语料，纯 Python 点积一次 20 µs 量级，比连数据库还快；
- 单测不需要容器，CI 不用等 Docker；
- 真实现（``PgVectorStore``）只影响 ``add`` / ``search`` 两个方法。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.errors import ConfigurationError


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


class VectorStore(Protocol):
    """存储后端的接口契约：两条实现必须同签名，否则换后端就是换语义。

    [TUTOR-JAVA] 相当于 interface，只有方法签名，没有实现。
    """

    def __len__(self) -> int: ...

    def add(self, chunk: Chunk, vector: Sequence[float]) -> None: ...

    def add_many(self, items: Iterable[tuple[Chunk, Sequence[float]]]) -> None: ...

    def search(
        self, vector: Sequence[float], *, top_k: int = 3, tenant_id: str = "default"
    ) -> tuple[Hit, ...]: ...


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


# 表名会拼进 SQL，所以只允许「小写字母开头 + 小写字母/数字/下划线」：把注入面压到零
_TABLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

_SQL_INSERT = """
INSERT INTO {table} (id, title, text, tags, tenant_id, embedding)
VALUES (%(id)s, %(title)s, %(text)s, %(tags)s::jsonb, %(tenant)s, %(vec)s::vector)
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title, text = EXCLUDED.text, tags = EXCLUDED.tags,
    tenant_id = EXCLUDED.tenant_id, embedding = EXCLUDED.embedding
"""

_SQL_SEARCH = """
SELECT id, title, text, tags, tenant_id, 1 - (embedding <=> %(vec)s::vector) AS score
FROM {table}
WHERE tenant_id = %(tenant)s
ORDER BY embedding <=> %(vec)s::vector
LIMIT %(k)s
"""


def _import_psycopg() -> Any:
    """延迟导入：没装 psycopg 的机器（CI、纯内存模式）不该因为这条 import 挂掉。"""
    try:
        import psycopg  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 只在缺依赖时触发
        raise ConfigurationError(
            "pgvector 后端需要 psycopg：pip install 'psycopg[binary]>=3.2'",
            details={"hint": "或把 retrieval.backend 改回 memory"},
        ) from exc
    return psycopg


def _vector_literal(vector: Sequence[float]) -> str:
    # pgvector 只认 '[0.1,0.2,...]' 这种文本；用 repr 保证精度不被截断
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class PgVectorStore:
    """pgvector 后端：块与向量进 PostgreSQL，排序交给库（HNSW 索引）。

    接口和 :class:`MemoryVectorStore` 完全一致，所以 ``Retriever`` 一行都不用改——
    换的是部署形态，不是检索语义。两条实现必须给出同一批分数，否则就是换错了。

    表名会拼进 SQL，所以构造函数用正则白名单校验（注入面为零），
    bandit 的 B608 是静态分析看不出这层校验，逐处标 ``nosec`` 并注明原因。
    """

    def __init__(self, dsn: str, dim: int, *, table: str = "chunks") -> None:
        if not _TABLE_PATTERN.fullmatch(table):
            raise ConfigurationError(f"表名非法：{table}（只允许小写字母/数字/下划线）")
        if dim <= 0:
            raise ConfigurationError("向量维度必须为正数")
        self.dim = dim
        self._dsn = dsn
        self._table = table
        self._conn: Any = None
        self._ready = False

    def __len__(self) -> int:
        self._ensure_schema()
        with self._conn.cursor() as cursor:
            # 表名来自构造函数里的正则白名单，静态分析看不出来，这里显式放行
            cursor.execute(f"SELECT COUNT(*) FROM {self._table}")  # nosec B608
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def add(self, chunk: Chunk, vector: Sequence[float]) -> None:
        self.add_many([(chunk, vector)])

    def add_many(self, items: Iterable[tuple[Chunk, Sequence[float]]]) -> None:
        self._ensure_schema()
        rows = []
        for chunk, vector in items:
            if len(vector) != self.dim:
                raise ValueError(f"向量维度不符：配置 {self.dim}，实际 {len(vector)}")
            rows.append(
                {
                    "id": chunk.id,
                    "title": chunk.title,
                    "text": chunk.text,
                    "tags": json.dumps(list(chunk.tags), ensure_ascii=False),
                    "tenant": chunk.tenant_id,
                    "vec": _vector_literal(vector),
                }
            )
        if not rows:
            return
        with self._conn.cursor() as cursor:
            # 表名白名单校验 + 值全部走 %(...)s 绑定
            cursor.executemany(_SQL_INSERT.format(table=self._table), rows)  # nosec B608

    def search(
        self, vector: Sequence[float], *, top_k: int = 3, tenant_id: str = "default"
    ) -> tuple[Hit, ...]:
        if top_k <= 0:
            raise ValueError("top_k 必须为正数")
        if len(vector) != self.dim:
            raise ValueError(f"查询向量维度不符：配置 {self.dim}，实际 {len(vector)}")
        self._ensure_schema()
        params = {"vec": _vector_literal(vector), "tenant": tenant_id, "k": top_k}
        with self._conn.cursor() as cursor:
            # 表名白名单校验 + 向量/租户/top_k 全部走绑定参数
            cursor.execute(_SQL_SEARCH.format(table=self._table), params)  # nosec B608
            rows = cursor.fetchall()
        hits = []
        for chunk_id, title, text, tags, row_tenant, score in rows:
            raw_tags = json.loads(tags) if isinstance(tags, str) else list(tags or ())
            hits.append(
                Hit(
                    chunk=Chunk(
                        id=chunk_id,
                        title=title,
                        text=text,
                        tags=tuple(raw_tags),
                        tenant_id=row_tenant,
                    ),
                    score=float(score),
                )
            )
        return tuple(hits)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._ready = False

    def _ensure_schema(self) -> None:
        """第一次用时建扩展/表/索引。幂等：反复建不会报错。"""
        if self._ready and self._conn is not None:
            return
        if self._conn is None:
            self._conn = _import_psycopg().connect(self._dsn, autocommit=True)
        table = self._table
        statements = [
            "CREATE EXTENSION IF NOT EXISTS vector",
            # 表名白名单 + 维度是整数校验过的
            f"""CREATE TABLE IF NOT EXISTS {table} (
                id text PRIMARY KEY,
                title text NOT NULL,
                text text NOT NULL,
                tags jsonb NOT NULL DEFAULT '[]'::jsonb,
                tenant_id text NOT NULL,
                embedding vector({self.dim}) NOT NULL
            )""",
            f"CREATE INDEX IF NOT EXISTS {table}_tenant_idx ON {table} (tenant_id)",
            # 余弦距离索引：与 MemoryVectorStore 的点积同口径（向量都归一化过）
            f"CREATE INDEX IF NOT EXISTS {table}_vec_idx ON {table} "
            f"USING hnsw (embedding vector_cosine_ops)",
        ]
        with self._conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        self._ready = True
