"""1.10b 契约：pgvector 后端与内存后端必须给出同一批结果。

这三条只在「有 psycopg + 有可用库」时跑：CI 里没有容器，自动 skip。
本地跑法：
    docker run -d --name sales-pg -e POSTGRES_PASSWORD=postgres -p 5482:5432 pgvector/pgvector:pg16
    PG_TEST_DSN=postgresql://postgres:postgres@localhost:5482/postgres \
        pytest tests/integration/test_pgvector_store.py
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("psycopg", reason="pgvector 后端需要 psycopg（pip install 'psycopg[binary]'）")

from app.retrieval.embedder import HashingEmbedder  # noqa: E402
from app.retrieval.store import Chunk, MemoryVectorStore, PgVectorStore  # noqa: E402

DSN = os.getenv("PG_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="未设置 PG_TEST_DSN，跳过真库用例")

DIM = 256
TABLE = "chunks_test_contract"


@pytest.fixture(scope="module")
def chunks() -> tuple[Chunk, ...]:
    rows = [
        Chunk(id="kb-1", title="免赔额", text="百万医疗免赔额一万元", tags=("医疗",)),
        Chunk(id="kb-2", title="等待期", text="重疾险等待期九十天"),
        Chunk(id="kb-3", title="别的租户", text="这是别家的知识块", tenant_id="tenant-b"),
    ]
    return tuple(rows)


@pytest.fixture(scope="module")
def stores(chunks):
    embedder = HashingEmbedder(dim=DIM)
    memory = MemoryVectorStore()
    pg = PgVectorStore(DSN, dim=DIM, table=TABLE)
    # 干净起步：表可能留着上一次的数据
    pg._ensure_schema()
    with pg._conn.cursor() as cursor:
        cursor.execute(f"DELETE FROM {TABLE}")
    items = [(chunk, embedder.embed(f"{chunk.title}。{chunk.text}")) for chunk in chunks]
    memory.add_many(items)
    pg.add_many(items)
    yield memory, pg
    pg.close()


def test_same_top1_and_score(stores) -> None:
    """同一查询在两条后端上必须给出同一 top1、同一分数（误差 < 1e-6）。"""
    memory, pg = stores
    embedder = HashingEmbedder(dim=DIM)
    vector = embedder.embed("重疾险等待期多久")

    mem_hits = memory.search(vector, top_k=2)
    pg_hits = pg.search(vector, top_k=2)

    assert [hit.chunk.id for hit in mem_hits] == [hit.chunk.id for hit in pg_hits]
    assert abs(mem_hits[0].score - pg_hits[0].score) < 1e-6


def test_tenant_filter_before_scoring(stores) -> None:
    """租户过滤在 SQL 里做：别家租户的块根本不进候选池。"""
    _memory, pg = stores
    embedder = HashingEmbedder(dim=DIM)
    hits = pg.search(embedder.embed("别家的知识块"), top_k=3, tenant_id="default")
    assert all(hit.chunk.tenant_id == "default" for hit in hits)
    assert "kb-3" not in [hit.chunk.id for hit in hits]


def test_upsert_is_idempotent(stores, chunks) -> None:
    """重复灌同一批语料不该翻倍（重启会反复 load_seed，这条是防膨胀的）。"""
    _memory, pg = stores
    embedder = HashingEmbedder(dim=DIM)
    pg.add_many([(chunks[0], embedder.embed("免赔额"))])
    pg.add_many([(chunks[0], embedder.embed("免赔额"))])
    with pg._conn.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE id = %(id)s", {"id": "kb-1"})
        count = cursor.fetchone()[0]
    assert count == 1
