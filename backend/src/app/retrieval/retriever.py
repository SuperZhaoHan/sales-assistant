"""检索层：加载语料 → 向量化 → 入索引 → 按问题召回。

三件事分开写（load / index / search），因为它们的替换节奏不同：
语料随行业换，embedder 随效果换，存储随部署换。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import yaml

from app.core.errors import ValidationError
from app.retrieval.embedder import Embedder, HashingEmbedder
from app.retrieval.store import Chunk, Hit, MemoryVectorStore


def load_chunks(path: str | Path) -> tuple[Chunk, ...]:
    """读 ``corpus/*.yaml``，返回知识块列表。"""
    file = Path(path)
    if not file.exists():
        raise ValidationError(f"语料文件不存在：{file.name}", details={"path": str(file)})
    payload = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    rows = payload.get("chunks") or []
    if not rows:
        raise ValidationError(f"语料为空：{file.name}", details={"path": str(file)})

    chunks = []
    for row in rows:
        missing = [key for key in ("id", "title", "text") if not row.get(key)]
        if missing:
            raise ValidationError(
                f"语料块缺字段：{row.get('id', '?')} 缺 {','.join(missing)}",
                details={"chunk": row.get("id"), "missing": missing},
            )
        chunks.append(
            Chunk(
                id=row["id"],
                title=row["title"],
                text=row["text"],
                tags=tuple(row.get("tags") or ()),
                tenant_id=row.get("tenant_id", "default"),
            )
        )
    ids = [chunk.id for chunk in chunks]
    if len(ids) != len(set(ids)):
        # [TUTOR-WHY] id 重复是致命的：引用编号靠它反查，重了之后「第 3 条引用」指哪条说不清
        duplicated = sorted({chunk_id for chunk_id in ids if ids.count(chunk_id) > 1})
        raise ValidationError("语料 id 重复", details={"ids": duplicated})
    return tuple(chunks)


class Retriever:
    """检索入口。对外只有一个 :meth:`search`，工具层不关心里面是内存还是 pgvector。"""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder or HashingEmbedder()
        self._store = MemoryVectorStore()

    @property
    def size(self) -> int:
        return len(self._store)

    # [TUTOR-WHY] 标题拼进向量里：客户说「重疾」时，标题「重疾险等待期」能补上字面匹配的分
    def index(self, chunks: Sequence[Chunk]) -> int:
        for chunk in chunks:
            # 标题和标签也参与向量：客户说「重疾」时，标题里的「重疾险」能补上一点分
            text = f"{chunk.title}。{chunk.text}"
            self._store.add(chunk, self._embedder.embed(text))
        return len(self._store)

    def search(
        self, query: str, *, top_k: int = 3, tenant_id: str = "default", min_score: float = 0.0
    ) -> tuple[Hit, ...]:
        query = query.strip()
        if not query:
            # [TUTOR-WHY] 空问题不返回「最像的几条」：那等于随机推荐，模型会当成证据用
            raise ValidationError("检索问题不能为空", details={"field": "query"})
        hits = self._store.search(
            self._embedder.embed(query), top_k=top_k, tenant_id=tenant_id
        )
        return tuple(hit for hit in hits if hit.score >= min_score)
