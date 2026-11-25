"""1.5 验收：语料能灌、能召回、按租户隔离、坏语料当场报错。

可失败点：
- 把「租户过滤」删掉 → test_cross_tenant_invisible 红；
- 把「id 重复检查」删掉 → test_duplicate_id_rejected 红；
- 把归一化删掉 → test_embedder_is_deterministic 仍绿，但 test_similar_texts_score_higher 会红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.retrieval.embedder import HashingEmbedder
from app.retrieval.retriever import Retriever, load_chunks
from app.retrieval.store import Chunk

CORPUS = Path(__file__).resolve().parents[2] / "src/app/domains/insurance/corpus/knowledge.yaml"


@pytest.fixture
def retriever() -> Retriever:
    r = Retriever()
    r.index(load_chunks(CORPUS))
    return r


def test_corpus_loaded() -> None:
    chunks = load_chunks(CORPUS)
    assert len(chunks) == 30
    assert chunks[0].id == "kb-001"


def test_embedder_is_deterministic() -> None:
    embedder = HashingEmbedder()
    first = embedder.embed("重疾险等待期")
    second = embedder.embed("重疾险等待期")
    # 跨进程也要一致，否则评测数字不可复现
    assert first == second
    assert len(first) == 256
    assert abs(sum(v * v for v in first) - 1.0) < 1e-9


def test_embedder_empty_text_is_zero_vector() -> None:
    embedder = HashingEmbedder()
    vector = embedder.embed("   ，。  ")
    assert set(vector) == {0.0}


def test_similar_texts_score_higher() -> None:
    embedder = HashingEmbedder()
    base = embedder.embed("重疾险等待期一般多久")
    near = embedder.embed("重疾险的等待期")
    far = embedder.embed("保单贷款额度")
    assert sum(a * b for a, b in zip(base, near, strict=True)) > sum(
        a * b for a, b in zip(base, far, strict=True)
    )


def test_search_returns_expected_chunk(retriever: Retriever) -> None:
    hits = retriever.search("重疾险的等待期一般多久？", top_k=3)
    assert hits
    assert hits[0].chunk.id == "kb-001"
    # 分数必须单调不增，否则「top_k=1」就不是最优
    assert all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1))


def test_top_k_caps_result(retriever: Retriever) -> None:
    assert len(retriever.search("保险", top_k=2)) == 2


def test_cross_tenant_invisible(retriever: Retriever) -> None:
    other = Chunk(id="kb-x1", title="别家条款", text="只属于另一个租户的内容", tenant_id="other")
    retriever.index((other,))
    hits = retriever.search("别家条款", tenant_id="default")
    assert all(hit.chunk.tenant_id == "default" for hit in hits)


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    file = tmp_path / "dup.yaml"
    file.write_text(
        "chunks:\n"
        "  - {id: kb-1, title: A, text: 甲}\n"
        "  - {id: kb-1, title: B, text: 乙}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as caught:
        load_chunks(file)
    assert caught.value.details["ids"] == ["kb-1"]


def test_missing_field_rejected(tmp_path: Path) -> None:
    file = tmp_path / "bad.yaml"
    file.write_text("chunks:\n  - {id: kb-1, title: A}\n", encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_chunks(file)
    assert "text" in str(caught.value)


def test_empty_query_rejected(retriever: Retriever) -> None:
    with pytest.raises(ValidationError):
        retriever.search("   ")
