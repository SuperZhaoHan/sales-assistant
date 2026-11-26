"""1.7 评测：该拒的拒没拒（拒答正确率）、该答的有没有被误拒（误拒率）。

跑法：``.venv/bin/python scripts/eval_refusal.py``

判定口径（只看检索侧，不调模型）：
- 问题 → 检索 top-3；
- top1 为空，或 top1 分数 < 阈值 → 判「拒答」；
- 该拒题：判拒 = 对；该答题：判拒 = 误拒。

为什么不调模型：拒答的第一道执行关卡就在检索侧。模型根本没被调起来，
就不存在「它编不编」的问题。用真模型再测一遍是另一步（1.8）。
"""

from __future__ import annotations

import yaml

from app.domains import load_domain
from app.retrieval.retriever import Retriever, load_chunks
from app.tools.knowledge_search import render

TOP_K = 3


def main() -> None:
    pack = load_domain("insurance")
    retriever = Retriever()
    for file in sorted((pack.root / "corpus").glob("*.yaml")):
        retriever.index(load_chunks(file))

    gold = yaml.safe_load((pack.root / "eval" / "gold.yaml").read_text(encoding="utf-8"))
    questions = gold["questions"]
    min_score = float(pack.rules["thresholds"]["retrieval_min_score"])
    answerable = [row for row in questions if row["expect"]]
    unanswerable = [row for row in questions if not row["expect"]]

    print(f"语料 {retriever.size} 条，阈值 {min_score}，共 {len(questions)} 题")
    print(f"该答 {len(answerable)} 题，该拒 {len(unanswerable)} 题\n")

    print("— 该拒的题：看顶分够不够 ——")
    refused = 0
    for row in unanswerable:
        hits = retriever.search(row["q"], top_k=TOP_K)
        top1 = hits[0] if hits else None
        ok = top1 is None or top1.score < min_score
        refused += int(ok)
        mark = "✓ 拒" if ok else "✗ 答了"
        where = f"{top1.chunk.id} ({top1.score:.2f})" if top1 else "无命中"
        print(f"{mark}  {row['q']}  → top1 {where}")

    print("\n— 该答的题：会不会被误拒 ——")
    false_refusals = 0
    for row in answerable:
        hits = retriever.search(row["q"], top_k=TOP_K)
        top1 = hits[0] if hits else None
        would_refuse = top1 is None or top1.score < min_score
        false_refusals += int(would_refuse)
        if would_refuse:
            where = f"{top1.chunk.id} ({top1.score:.2f})" if top1 else "无命中"
            print(f"✗ 误拒  {row['q']}  → top1 {where}")

    refusal_rate = refused / len(unanswerable) * 100
    false_refusal_rate = false_refusals / len(answerable) * 100
    print(f"\n拒答正确率 = {refused}/{len(unanswerable)} = {refusal_rate:.0f}%")
    print(f"误拒率     = {false_refusals}/{len(answerable)} = {false_refusal_rate:.0f}%")

    print("\n— 阈值扫描：不同阈值下两个率怎么变 —")
    print(f"{'阈值':<8}{'拒答正确':<12}{'误拒':<10}")
    for threshold in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50):
        refused_n = 0
        for row in unanswerable:
            hits = retriever.search(row["q"], top_k=TOP_K)
            refused_n += int(not hits or hits[0].score < threshold)
        false_n = 0
        for row in answerable:
            hits = retriever.search(row["q"], top_k=TOP_K)
            false_n += int(not hits or hits[0].score < threshold)
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
