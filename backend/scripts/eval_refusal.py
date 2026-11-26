"""1.7 评测：该拒的拒没拒（拒答正确率）、该答的有没有被误拒（误拒率）。

跑法：
    ``.venv/bin/python scripts/eval_refusal.py``                  哈希假向量（不联网）
    ``.venv/bin/python scripts/eval_refusal.py --embedder real``  真 embedding

判定口径（只看检索侧，不调模型）：
- 问题 → 检索 top-3；
- top1 为空，或 top1 分数 < 阈值 → 判「拒答」；
- 该拒题：判拒 = 对；该答题：判拒 = 误拒。

为什么不调模型：拒答的第一道执行关卡就在检索侧。模型根本没被调起来，
就不存在「它编不编」的问题。用真模型再测一遍是另一步（1.8）。
"""

from __future__ import annotations

import argparse
import statistics

import yaml
from eval_common import build_embedder, describe, effective_min_score

from app.domains import load_domain
from app.retrieval.retriever import Retriever, load_chunks
from app.tools.knowledge_search import render

TOP_K = 3
# 扫描上界放到 0.65：哈希向量时代 0.45 就误拒 16/17，真向量下分数整体会抬高
SCAN_THRESHOLDS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65)


def main() -> None:
    parser = argparse.ArgumentParser(description="拒答正确率 + 误拒率 + 阈值扫描")
    parser.add_argument(
        "--embedder",
        choices=("hash", "real"),
        default="hash",
        help="hash=本地假向量（不联网）；real=按 .env 调真 embedding",
    )
    args = parser.parse_args()

    pack = load_domain("insurance")
    embedder = build_embedder(args.embedder)
    retriever = Retriever(embedder)
    for file in sorted((pack.root / "corpus").glob("*.yaml")):
        retriever.index(load_chunks(file))

    gold = yaml.safe_load((pack.root / "eval" / "gold.yaml").read_text(encoding="utf-8"))
    questions = gold["questions"]
    min_score = effective_min_score(pack)
    answerable = [row for row in questions if row["expect"]]
    unanswerable = [row for row in questions if not row["expect"]]

    # 每题只检索一次：真 embedding 下「一次检索 = 一次 HTTP 请求」，
    # 阈值扫描必须复用同一批分数，否则 10 个阈值 × 24 题 = 240 次请求
    top1: dict[str, tuple[str, float] | None] = {}
    for row in questions:
        hits = retriever.search(row["q"], top_k=TOP_K)
        top1[row["q"]] = (hits[0].chunk.id, hits[0].score) if hits else None

    print(f"embedder {describe(embedder)}，语料 {retriever.size} 条，阈值 {min_score}")
    print(f"该答 {len(answerable)} 题，该拒 {len(unanswerable)} 题\n")

    print("— 该拒的题：看顶分够不够 —")
    refused = 0
    for row in unanswerable:
        hit = top1[row["q"]]
        ok = hit is None or hit[1] < min_score
        refused += int(ok)
        mark = "✓ 拒" if ok else "✗ 答了"
        where = f"{hit[0]} ({hit[1]:.2f})" if hit else "无命中"
        print(f"{mark}  {row['q']}  → top1 {where}")

    print("\n— 该答的题：会不会被误拒 —")
    false_refusals = 0
    for row in answerable:
        hit = top1[row["q"]]
        would_refuse = hit is None or hit[1] < min_score
        false_refusals += int(would_refuse)
        if would_refuse:
            where = f"{hit[0]} ({hit[1]:.2f})" if hit else "无命中"
            print(f"✗ 误拒  {row['q']}  → top1 {where}")

    refusal_rate = refused / len(unanswerable) * 100
    false_refusal_rate = false_refusals / len(answerable) * 100
    print(f"\n拒答正确率 = {refused}/{len(unanswerable)} = {refusal_rate:.0f}%")
    print(f"误拒率     = {false_refusals}/{len(answerable)} = {false_refusal_rate:.0f}%")

    # 两组分数各落在哪、重叠多宽：这是「能不能靠一个阈值分开」的唯一判据
    ans = sorted(hit[1] for row in answerable if (hit := top1[row["q"]]) is not None)
    rej = sorted(hit[1] for row in unanswerable if (hit := top1[row["q"]]) is not None)
    if ans and rej:
        low_max = max(rej)
        high_min = ans[0]
        width = low_max - high_min
        overlap = f"{high_min:.2f}–{low_max:.2f}（宽 {width:.2f}）" if width > 0 else "无重叠"
        print(
            f"\n该答 top1：{ans[0]:.2f}–{ans[-1]:.2f}，中位 {statistics.median(ans):.2f}"
            f"\n该拒 top1：{rej[0]:.2f}–{rej[-1]:.2f}，中位 {statistics.median(rej):.2f}"
            f"\n重叠区：{overlap}"
        )

    print("\n— 阈值扫描：不同阈值下两个率怎么变 —")
    print(f"{'阈值':<8}{'拒答正确':<12}{'误拒':<10}")
    for threshold in SCAN_THRESHOLDS:
        refused_n = 0
        for row in unanswerable:
            hit = top1[row["q"]]
            refused_n += int(hit is None or hit[1] < threshold)
        false_n = 0
        for row in answerable:
            hit = top1[row["q"]]
            false_n += int(hit is None or hit[1] < threshold)
        star = "  ← 当前" if abs(threshold - min_score) < 1e-9 else ""
        print(
            f"{threshold:<8.2f}{refused_n}/{len(unanswerable):<9}"
            f"{false_n}/{len(answerable):<8}{star}"
        )

    print("\n— 闲聊兜底话术（该拒时给模型的原话）—")
    print(
        render(
            "别家同类产品比你们便宜多少",
            (),
            min_score=min_score,
            refusal_text=pack.rules["refusal"]["text"],
        )
    )


if __name__ == "__main__":
    main()
