"""检索层：加载语料 → 向量化 → 入索引 → 按问题召回。

三件事分开写（load / index / search），因为它们的替换节奏不同：
语料随行业换，embedder 随效果换，存储随部署换。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from app.core.errors import ConfigurationError, ValidationError
from app.retrieval.embedder import Embedder, HashingEmbedder
from app.retrieval.fingerprint import content_sha, embed_text
from app.retrieval.store import Chunk, Hit, MemoryVectorStore, VectorStore

IndexingMode = Literal["sync", "readonly"]


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


@dataclass(frozen=True, slots=True)
class IndexStats:
    """一次同步的账。added/updated 就是真正掏钱算向量的条数。"""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    total: int = 0

    @property
    def embedded(self) -> int:
        return self.added + self.updated


def load_corpus(directory: str | Path) -> tuple[Chunk, ...]:
    """读整个语料目录并做跨文件去重校验：同一 id 出现在两个文件里同样是致命的。"""
    files = sorted(Path(directory).glob("*.yaml"))
    if not files:
        name = Path(directory).name
        raise ValidationError(f"语料目录为空：{name}", details={"path": str(directory)})
    chunks = [chunk for file in files for chunk in load_chunks(file)]
    ids = [chunk.id for chunk in chunks]
    if len(ids) != len(set(ids)):
        duplicated = sorted({chunk_id for chunk_id in ids if ids.count(chunk_id) > 1})
        raise ValidationError("语料 id 重复（跨文件）", details={"ids": duplicated})
    return tuple(chunks)


class Retriever:
    """检索入口。对外只有一个 :meth:`search`，工具层不关心里面是内存还是 pgvector。"""

    def __init__(self, embedder: Embedder | None = None, store: VectorStore | None = None) -> None:
        self._embedder = embedder or HashingEmbedder()
        # 默认内存表；真库由调用方注入（PgVectorStore 与它实现同一个 Protocol）
        self._store: VectorStore = store if store is not None else MemoryVectorStore()
        # 上一次同步的账：启动日志靠它回答「这次到底算了几条向量」
        self.last_stats: IndexStats | None = None

    @property
    def size(self) -> int:
        return len(self._store)

    # [TUTOR-WHY] 标题拼进向量里：客户说「重疾」时，标题「重疾险等待期」能补上字面匹配的分
    def index(self, chunks: Sequence[Chunk]) -> int:
        """全量重算：条数不变也照样算一遍向量。评测/脚本/测试用，启动路径不用它。"""
        texts = [embed_text(chunk.title, chunk.text) for chunk in chunks]
        # 一次批量拿全部向量：真模型按 HTTP 计费/计时，逐条 embed 会把 30 条语料变成 30 次请求
        vectors = self._embedder.embed_many(texts)
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._store.add(chunk, vector)
        return len(self._store)

    def sync(
        self, chunks: Sequence[Chunk], *, prune: bool = True, rebuild: bool = False
    ) -> IndexStats:
        """增量同步：只给「新增 / 正文变了」的条目算向量，并清掉语料里已经没有的条目。

        判据是正文指纹而不是 id：改正文不改 id 才是语料更新的常态（id 是引用锚点）。
        ``rebuild=True`` 先清空再全量算，用于换模型/换维度这种「旧向量整体作废」的场合。
        """
        chunks = tuple(chunks)
        if rebuild:
            self._store.prune(())
        known = self._store.content_shas()
        added = [chunk for chunk in chunks if chunk.id not in known]
        updated = [
            chunk
            for chunk in chunks
            if chunk.id in known and known[chunk.id] != content_sha(chunk.title, chunk.text)
        ]
        todo = added + updated
        if todo:
            texts = [embed_text(chunk.title, chunk.text) for chunk in todo]
            vectors = self._embedder.embed_many(texts)
            self._store.add_many(zip(todo, vectors, strict=True))
        removed = self._store.prune([chunk.id for chunk in chunks]) if prune else 0
        self.last_stats = IndexStats(
            added=len(added),
            updated=len(updated),
            unchanged=len(chunks) - len(todo),
            removed=removed,
            total=len(self._store),
        )
        return self.last_stats

    def audit(self, chunks: Sequence[Chunk]) -> None:
        """只读模式的对账：库必须是语料的精确投影，差一条就当场失败，不许带病启动。

        为什么要「多余」也算不一致：多余行不会进检索结果，但会让「库里有什么」与语料
        长期偏离，等到某天按 id 反查引用时才暴露。
        """
        chunks = tuple(chunks)
        known = self._store.content_shas()
        ids = {chunk.id for chunk in chunks}
        missing = sorted(ids - set(known))
        stale = sorted(
            chunk.id
            for chunk in chunks
            if chunk.id in known and known[chunk.id] != content_sha(chunk.title, chunk.text)
        )
        extra = sorted(set(known) - ids)
        if missing or stale or extra:
            raise ConfigurationError(
                "检索库与语料不一致（启动只读模式不负责建库）",
                details={
                    "missing": missing[:10],
                    "stale": stale[:10],
                    "extra": extra[:10],
                    "hint": "先跑 scripts/build_index.py 重建索引，再把 indexing 改回 readonly",
                },
            )

    def search(
        self, query: str, *, top_k: int = 3, tenant_id: str = "default", min_score: float = 0.0
    ) -> tuple[Hit, ...]:
        query = query.strip()
        if not query:
            # [TUTOR-WHY] 空问题不返回「最像的几条」：那等于随机推荐，模型会当成证据用
            raise ValidationError("检索问题不能为空", details={"field": "query"})
        hits = self._store.search(self._embedder.embed(query), top_k=top_k, tenant_id=tenant_id)
        return tuple(hit for hit in hits if hit.score >= min_score)
