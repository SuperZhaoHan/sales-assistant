"""1.10c 验收：启动期只做增量索引，readonly 模式只对账不写库。

可失败点（每条对应一处实现）：
- 把 ``sync`` 里的指纹比对改回「id 在库里就跳过」→ test_changed_text_reembeds_that_row 红；
- 把 ``prune`` 删掉 → test_removed_chunk_is_pruned 红；
- 把 ``readonly`` 的对账删掉 → test_readonly_refuses_stale_index 红；
- 把 ``rebuild`` 的清空删掉 → test_rebuild_recomputes_everything 红。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.errors import ConfigurationError
from app.retrieval.embedder import HashingEmbedder
from app.retrieval.retriever import Retriever, load_corpus
from app.retrieval.store import Chunk, MemoryVectorStore

CORPUS = Path(__file__).resolve().parents[2] / "src/app/domains/insurance/corpus"


class CountingEmbedder:
    """数「这次到底算了几条向量」：不计数就等于没验证这条债修没修好。"""

    def __init__(self) -> None:
        self._inner = HashingEmbedder()
        self.dim = self._inner.dim
        self.calls = 0

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls += 1
        return self._inner.embed(text)

    def embed_many(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls += len(texts)
        return self._inner.embed_many(texts)


@pytest.fixture
def chunks() -> tuple[Chunk, ...]:
    return load_corpus(CORPUS)


@pytest.fixture
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


@pytest.fixture
def retriever(chunks: tuple[Chunk, ...], embedder: CountingEmbedder) -> Retriever:
    r = Retriever(embedder, MemoryVectorStore())
    r.sync(chunks)
    return r


def test_first_sync_embeds_all(chunks: tuple[Chunk, ...], retriever: Retriever) -> None:
    assert retriever.last_stats is not None
    assert retriever.last_stats.embedded == len(chunks) == 30
    assert retriever.size == 30


def test_second_sync_embeds_nothing(chunks: tuple[Chunk, ...], retriever: Retriever) -> None:
    retriever.sync(chunks)
    assert retriever.last_stats is not None
    assert retriever.last_stats.embedded == 0
    assert retriever.last_stats.unchanged == 30


def test_changed_text_reembeds_that_row(
    chunks: tuple[Chunk, ...], embedder: CountingEmbedder, retriever: Retriever
) -> None:
    """正文改了、id 没改——这是语料更新的常态，必须被认出来。"""
    target = replace(chunks[0], text="重疾险等待期改成 100 天，仅此一条变更")
    retriever.sync((target, *chunks[1:]))
    assert retriever.last_stats is not None
    assert retriever.last_stats.embedded == 1
    assert retriever.last_stats.updated == 1
    assert embedder.calls == 31  # 首轮 30 + 这次 1
    # 不只是指纹变了：向量也得重算，检索结果里必须是新正文
    hit = retriever.search("重疾险等待期改成 100 天", top_k=1)[0]
    assert hit.chunk.text == target.text


def test_removed_chunk_is_pruned(chunks: tuple[Chunk, ...], retriever: Retriever) -> None:
    retriever.sync(chunks[:-1])
    assert retriever.last_stats is not None
    assert retriever.last_stats.removed == 1
    assert retriever.size == 29


def test_rebuild_recomputes_everything(
    chunks: tuple[Chunk, ...], embedder: CountingEmbedder, retriever: Retriever
) -> None:
    retriever.sync(chunks, rebuild=True)
    assert retriever.last_stats is not None
    assert retriever.last_stats.embedded == 30
    assert embedder.calls == 60
    assert retriever.size == 30


def test_readonly_accepts_consistent_index(
    chunks: tuple[Chunk, ...], embedder: CountingEmbedder, retriever: Retriever
) -> None:
    retriever.audit(chunks)
    assert embedder.calls == 30  # 对账不花一次 embedding


def test_readonly_refuses_stale_index(chunks: tuple[Chunk, ...], retriever: Retriever) -> None:
    """启动只读模式最怕的事：语料加了一条，库没重建，服务照样起来还少一条知识。"""
    extra = replace(chunks[-1], id="kb-031", title="新增条款", text="只有语料里有、库里没有")
    with pytest.raises(ConfigurationError) as caught:
        retriever.audit((*chunks, extra))
    assert caught.value.details["missing"] == ["kb-031"]
    assert "build_index.py" in caught.value.details["hint"]


def test_readonly_refuses_changed_text(chunks: tuple[Chunk, ...], retriever: Retriever) -> None:
    changed = replace(chunks[0], text="正文换了但库还是旧向量")
    with pytest.raises(ConfigurationError) as caught:
        retriever.audit((changed, *chunks[1:]))
    assert caught.value.details["stale"] == [chunks[0].id]
