"""1.10b 验收：把内存索引换成 pgvector，验证「存储换了、检索语义没换」。

判据（三条，任一条不过就是换错了）：
1. 两个后端给出的 top1 chunk_id 完全一致；
2. top1 分数差 < 1e-6（余弦 vs 内积在归一化后应当数值相同）；
3. hit@3 数字一致。

跑法（先在 backend/ 起容器并装 psycopg）：
    .venv/bin/python scripts/verify_pgvector.py                # 哈希向量，不联网
    .venv/bin/python scripts/verify_pgvector.py --embedder real # 真 bge-m3
"""

from __future__ import annotations

import argparse
import os
import time

import yaml
from eval_common import build_embedder, describe

from app.domains import load_domain
from app.retrieval.retriever import Retriever, load_chunks
from app.retrieval.store import MemoryVectorStore, PgVectorStore

TOP_K = 3
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5482/postgres"


def main() -> None:
    parser = argparse.ArgumentParser(description="内存索引 vs pgvector 一致性验证")
    parser.add_argument("--embedder", choices=("hash", "real"), default="hash")
    parser.add_argument("--dsn", default=os.getenv("PG_TEST_DSN", DEFAULT_DSN))
    parser.add_argument("--table", default="chunks_check")
    args = parser.parse_args()

    pack = load_domain("insurance")
    embedder = build_embedder(args.embedder)
    chunks = [
        chunk
        for file in sorted((pack.root / "corpus").glob("*.yaml"))
        for chunk in load_chunks(file)
    ]

    memory = Retriever(embedder, MemoryVectorStore())
    pg = Retriever(embedder, PgVectorStore(args.dsn, dim=embedder.dim, table=args.table))

    started = time.perf_counter()
    memory.index(chunks)
    mem_index_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    pg.index(chunks)
    pg_index_ms = (time.perf_counter() - started) * 1000

    gold = yaml.safe_load((pack.root / "eval" / "gold.yaml").read_text(encoding="utf-8"))
    answerable = [row for row in gold["questions"] if row["expect"]]

    mismatched: list[str] = []
    score_diffs: list[float] = []
    mem_hits = 0
    pg_hits = 0
    for row in answerable:
        mem = memory.search(row["q"], top_k=TOP_K)
        pgres = pg.search(row["q"], top_k=TOP_K)
        mem_hits += int(any(hit.chunk.id in row["expect"] for hit in mem))
        pg_hits += int(any(hit.chunk.id in row["expect"] for hit in pgres))
        if mem and pgres:
            score_diffs.append(abs(mem[0].score - pgres[0].score))
            if mem[0].chunk.id != pgres[0].chunk.id:
                mismatched.append(f"{row['q']}：mem={mem[0].chunk.id} pg={pgres[0].chunk.id}")

    print(f"embedder {describe(embedder)}，语料 {len(chunks)} 条")
    print(f"建索引：memory {mem_index_ms:.1f} ms / pgvector {pg_index_ms:.1f} ms")
    print(f"hit@3 ：memory {mem_hits}/{len(answerable)} / pgvector {pg_hits}/{len(answerable)}")
    print(f"top1 分数最大偏差 {max(score_diffs, default=0.0):.2e}")

    # 单次检索延迟：memory 走本地点积，pgvector 多一次本地 socket 往返
    started = time.perf_counter()
    rounds = 50
    for _ in range(rounds):
        memory.search("重疾险等待期多久", top_k=TOP_K)
    mem_us = (time.perf_counter() - started) / rounds * 1e6
    started = time.perf_counter()
    for _ in range(rounds):
        pg.search("重疾险等待期多久", top_k=TOP_K)
    pg_us = (time.perf_counter() - started) / rounds * 1e6
    print(f"单次检索：memory {mem_us:.0f} µs / pgvector {pg_us:.0f} µs（各 {rounds} 次平均）")

    if mismatched:
        print("\n✗ 有不一致：")
        for line in mismatched:
            print("  " + line)
    else:
        print("\n✓ top1 chunk_id 全部一致")

    if pg_hits != mem_hits:
        print("✗ hit@3 数字不一致 —— 存储换了语义也换了")
    else:
        print("✓ hit@3 数字一致")


if __name__ == "__main__":
    main()
