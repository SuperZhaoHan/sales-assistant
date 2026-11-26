"""1.8 评测：一次跑出三组数字（摘要引用 / 检索命中 / 拒答）。

跑法：
- 不花钱的基线：``.venv/bin/python scripts/eval_summary.py``
  （provider=fake，摘要指标会是 0，属正常）
- 接真模型：
    export APP__LLM__PROVIDER=openai_compat
    export APP__LLM__MODEL=<模型名>
    export APP__LLM__BASE_URL=<https://.../v1>
    export APP__LLM__API_KEY_REF=secret://llm/api_key
    export LLM_API_KEY=<你的 key>
    .venv/bin/python scripts/eval_summary.py

口径说明（必须和数字一起讲）：
- 有引用率：4 个客户里，有几份摘要至少出现一个 [F*]；
- 可追溯率：答案里的引用能查回原文的比例，没有引用时为 None（不算 100%）；
- 幻觉日期：摘要正文出现的、不在 facts 里的日期 —— 有引用但正文仍可能编；
- hit@3 与拒答率来自 gold.yaml 的 24 道题，和摘要组互不影响。
"""

from __future__ import annotations

import re
import time

import yaml
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.domains import load_domain
from app.main import create_app

CUSTOMERS = ("张伟", "李娜", "陈建国", "王芳")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def _summary_metrics(client: TestClient) -> tuple[dict, float, int]:
    """跑摘要组，返回（指标，总成本，总 token）。"""
    rows = []
    total_cost = 0.0
    total_tokens = 0
    for name in CUSTOMERS:
        started = time.perf_counter()
        data = client.post("/v1/summary", json={"customer_name": name}).json()["data"]
        elapsed_ms = (time.perf_counter() - started) * 1000

        facts = set(data["facts"])
        # 正文里出现的日期，只要不在 facts 里就算幻觉
        dates = set(DATE_PATTERN.findall(data["text"])) - facts
        rows.append(
            {
                "客户": name,
                "引用数": len(data["citations"]),
                "可追溯率": data["traceable_rate"],
                "幻觉日期": ",".join(sorted(dates)) or "-",
                "token": data["usage"]["total_tokens"],
                "耗时ms": round(elapsed_ms, 1),
            }
        )
        total_cost += data["usage"]["cost_usd"]
        total_tokens += data["usage"]["total_tokens"]
    return rows, total_cost, total_tokens


def _retrieval_metrics(app) -> tuple[int, int, int, int, int]:
    """hit@3 与拒答：直接打检索器，不经过模型。"""
    pack = load_domain("insurance")
    gold = yaml.safe_load((pack.root / "eval" / "gold.yaml").read_text(encoding="utf-8"))
    questions = gold["questions"]
    min_score = float(pack.rules["thresholds"]["retrieval_min_score"])
    retriever = app.state.retriever

    answerable = [row for row in questions if row["expect"]]
    unanswerable = [row for row in questions if not row["expect"]]

    hits = 0
    for row in answerable:
        found = [hit.chunk.id for hit in retriever.search(row["q"], top_k=3)]
        hits += int(any(chunk_id in found for chunk_id in row["expect"]))

    refused = 0
    for row in unanswerable:
        top = retriever.search(row["q"], top_k=3)
        refused += int(not top or top[0].score < min_score)

    false_refused = 0
    for row in answerable:
        top = retriever.search(row["q"], top_k=3)
        false_refused += int(not top or top[0].score < min_score)

    return hits, len(answerable), refused, len(unanswerable), false_refused


def main() -> None:
    settings = load_config()
    print(f"provider={settings.llm.provider}  model={settings.llm.model}")
    if settings.llm.provider == "fake":
        print("⚠ fake provider：下面的摘要指标不是模型表现，只证明脚本能跑通\n")

    app = create_app(settings)
    with TestClient(app) as client:
        rows, cost, tokens = _summary_metrics(client)
        print("— 摘要组（4 位客户）—")
        print(f"{'客户':<8}{'引用数':<8}{'可追溯率':<12}{'幻觉日期':<14}{'token':<8}{'耗时ms'}")
        for row in rows:
            rate = "-" if row["可追溯率"] is None else f"{row['可追溯率']:.2f}"
            print(
                f"{row['客户']:<8}{row['引用数']:<8}{rate:<12}"
                f"{row['幻觉日期']:<14}{row['token']:<8}{row['耗时ms']}"
            )

        cited = sum(1 for row in rows if row["引用数"] > 0)
        all_refs = sum(row["引用数"] for row in rows)

        hits, answerable_n, refused, unanswerable_n, false_refused = _retrieval_metrics(app)

        print(f"\n有引用率     = {cited}/{len(CUSTOMERS)}")
        print(f"引用总条数   = {all_refs}")
        print(f"成本合计     = ${cost:.6f}，token {tokens}")

        print("\n— 检索组（gold.yaml 24 题）—")
        print(f"hit@3        = {hits}/{answerable_n} = {hits / answerable_n * 100:.0f}%")
        print(f"拒答正确率   = {refused}/{unanswerable_n}")
        print(f"误拒率       = {false_refused}/{answerable_n}")

        if settings.llm.provider == "fake":
            print("\n以上摘要指标在 fake 下无意义：")
            print("换真 provider 后重跑同一脚本，数字会自动变成模型表现。")


if __name__ == "__main__":
    main()
