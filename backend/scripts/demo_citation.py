"""1.6 演示：一段摘要里的编号，能不能逐条查回真实数据块。

跑法：``.venv/bin/python scripts/demo_citation.py``
这里用一段「模型典型输出」做样例，衡量的是**判定器**靠不靠谱；
真实模型的可追溯率要接真 provider 才能测（fake 只会复述指令，不出引用）。
"""

from __future__ import annotations

from app.agent.brief import build_brief
from app.agent.citation import build_catalog, resolve_citations
from app.domains import load_domain
from app.tools.bootstrap import open_store

SAMPLE = """【阶段】C 已报价待决策——重疾险 2026-03-01 到期且仍在待续保 [F1][F3]
【建议动作】本周内电话确认续保意向，并说明失效险种的影响 [F2]
【话术】张伟您好，您的重疾险 3 月 1 日到期，我们本周安排一次续保确认 [F1]
【依据】
[F1] 2026-03-01
[F3] 待续保
[F9] 预计下月成交"""

NO_CITATION = "【阶段】客户快到期了，建议尽快联系。"


def main() -> None:
    pack = load_domain("insurance")
    store = open_store(":memory:", pack.root / "seed")
    brief = build_brief(store, "张伟")
    catalog = build_catalog(brief.facts)

    print("— 后端发出的编号表（模型只能引用这里）—")
    for label, (kind, ref_id, source) in catalog.items():
        print(f"{label}  {kind:<8} {ref_id:<6} {source}")
    print()

    print("— 一段模型输出，逐条反查 —")
    citations, rate = resolve_citations(SAMPLE, catalog)
    for citation in citations:
        mark = "✓" if citation.resolved else "✗"
        source = citation.source or "（查不到）"
        print(f"{mark} {citation.label:<5} kind={citation.kind:<8} source={source}")
    print(f"\n可追溯率：{rate}（{sum(c.resolved for c in citations)}/{len(citations)}）\n")

    print("— 一段完全没引用的输出 —")
    citations2, rate2 = resolve_citations(NO_CITATION, catalog)
    print(f"引用数={len(citations2)}  可追溯率={rate2}  ← None 而不是 100%\n")

    print("— 这次真实发给模型的 facts 段 —")
    print(brief.variables()["facts"])

    store.close()


if __name__ == "__main__":
    main()
