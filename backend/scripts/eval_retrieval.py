"""1.5 评测：跑一遍金标集，打出 hit@3 与「该拒答题」的分数分布。

跑法：``.venv/bin/python scripts/eval_retrieval.py``
输出分两段：逐题诊断（哪题没命中、top1 是谁）和汇总数字。
"""

from __future__ import annotations

import time

import yaml

from app.domains import load_domain
from app.retrieval.retriever import Retriever, load_chunks

TOP_K = 3


def main() -> None:
    pack = load_domain("insurance")
    retriever = Retriever()

    started = time.perf_counter()
    for file in sorted((pack.root / "corpus").glob("*.yaml")):
        retriever.index(load_chunks(file))
    index_ms = (time.perf_counter() - started) * 1000

    gold = yaml.safe_load((pack.root / "eval" / "gold.yaml").read_text(encoding="utf-8"))
    questions = gold["questions"]
    min_score = float(pack.rules["thresholds"]["retrieval_min_score"])

    print(f"语料 {retriever.size} 条，建索引 {index_ms:.1f} ms，阈值 {min_score}\n")

    answerable = [row for row in questions if row["expect"]]
    unanswerable = [row for row in questions if not row["expect"]]

    hits = 0
    print("— 可回答题（判定：top3 里出现任一 expect）—")
    for row in answerable:
        results = retriever.search(row["q"], top_k=TOP_K)
        ids = [hit.chunk.id for hit in results]
        top1 = results[0] if results else None
        ok = any(chunk_id in ids for chunk_id in row["expect"])
        hits += int(ok)
        mark = "✓" if ok else "✗"
        where = f"top1 {top1.chunk.id} ({top1.score:.2f})" if top1 else "无命中"
        print(f"{mark} {row['q']}  → {where}")
    hit_at_3 = hits / len(answerable) * 100

    print("\n— 该拒答题（看它的最高分，用来定阈值）—")
    for row in unanswerable:
        results = retriever.search(row["q"], top_k=TOP_K)
        top1 = results[0] if results else None
        print(f"  {row['q']}  → top1 {top1.chunk.id if top1 else '-'} ({top1.score:.2f})")

    # 单次检索耗时
    rounds = 200
    started = time.perf_counter()
    for _ in range(rounds):
        retriever.search("重疾险等待期多久", top_k=TOP_K)
    per_call_us = (time.perf_counter() - started) / rounds * 1e6

    print(f"\nhit@{TOP_K} = {hits}/{len(answerable)} = {hit_at_3:.0f}%")
    print(f"单次检索 {per_call_us:.0f} 微秒（{rounds} 次平均，{retriever.size} 条语料）")


if __name__ == "__main__":
    main()
