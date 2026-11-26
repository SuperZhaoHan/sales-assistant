"""1.8 评测：摘要引用 / 检索命中 / 拒答，一次跑出三组数字并留下证据。

跑法：
- 只跑数字（不落盘）：``.venv/bin/python scripts/eval_summary.py``
- 多轮 + 落盘证据：``.venv/bin/python scripts/eval_summary.py --rounds 8 --dump``
  产出 ``backend/reports/<日期>-<模型>/``：index.md（总表）、issues.md（问题句）、
  raw-<客户>-<轮>.json（每份响应的原文与 facts，谁都能重判）。

口径说明（必须和数字一起讲）：
- 有引用率：出现了至少一个 [F*] 的摘要数 / 总摘要数；
- 可追溯率：引用能查回编号表的比例，无引用时 None（不算 100%）；
- 编造日期：正文出现、但 facts 里没有的日期 —— 只证编号存在不够，正文也可能编；
- hit@3 与拒答来自 gold.yaml 的 24 道题，不依赖模型。
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.agent.dates import sentences_with, ungrounded
from app.core.config import load_config
from app.domains import load_domain
from app.main import create_app

CUSTOMERS = ("张伟", "李娜", "陈建国", "王芳")
REPORT_ROOT = Path(__file__).resolve().parents[1] / "reports"


def _one_call(client: TestClient, name: str) -> tuple[dict, float]:
    started = time.perf_counter()
    payload = client.post("/v1/summary", json={"customer_name": name}).json()["data"]
    return payload, round((time.perf_counter() - started) * 1000, 1)


def _analyze(payload: dict) -> dict:
    """把一份响应转成一行指标 + 疑点列表。"""
    facts = payload["facts"]
    suspicious = ungrounded(payload["text"], facts)
    sentences = [
        sentence for _, iso in suspicious for sentence in sentences_with(payload["text"], iso)
    ]
    # 中文写法（2026 年 3 月 1 日）和 ISO 写法（2026-03-01）是同一件事，
    # 去重时按 ISO 形式走，避免同一句话被记两遍
    unique: dict[str, list[str]] = {}
    for raw, iso in suspicious:
        unique.setdefault(iso, []).append(raw)
    return {
        "引用数": len(payload["citations"]),
        "可追溯率": payload["traceable_rate"],
        "疑点": [{"iso": iso, "原文": raws, "句子": sentences} for iso, raws in unique.items()],
        "token": payload["usage"]["total_tokens"],
        "成本": payload["usage"]["cost_usd"],
    }


def _retrieval_metrics(app) -> dict[str, str]:
    """hit@3 与拒答：直接打检索器，不经过模型。"""
    pack = load_domain("insurance")
    gold = yaml.safe_load((pack.root / "eval" / "gold.yaml").read_text(encoding="utf-8"))
    questions = gold["questions"]
    min_score = float(pack.rules["thresholds"]["retrieval_min_score"])
    retriever = app.state.retriever

    answerable = [row for row in questions if row["expect"]]
    unanswerable = [row for row in questions if not row["expect"]]

    hits = sum(
        int(
            any(
                cid in [hit.chunk.id for hit in retriever.search(r["q"], top_k=3)]
                for cid in r["expect"]
            )
        )
        for r in answerable
    )
    refused = sum(
        int(not (top := retriever.search(r["q"], top_k=3)) or top[0].score < min_score)
        for r in unanswerable
    )
    false_refused = sum(
        int(not (top := retriever.search(r["q"], top_k=3)) or top[0].score < min_score)
        for r in answerable
    )
    return {
        "hit@3": f"{hits}/{len(answerable)} = {hits / len(answerable) * 100:.0f}%",
        "拒答正确率": f"{refused}/{len(unanswerable)}",
        "误拒率": f"{false_refused}/{len(answerable)}",
    }


def _write_report(runs: list[dict], retrieval: dict[str, str], provider: str, model: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d")
    folder = REPORT_ROOT / f"{stamp}-{model}"
    folder.mkdir(parents=True, exist_ok=True)

    for run in runs:
        raw = folder / f"raw-{run['客户']}-{run['轮次']:02d}.json"
        raw.write_text(
            json.dumps(run["响应"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    total = len(runs)
    cited = sum(1 for run in runs if run["指标"]["引用数"] > 0)
    issues = [run for run in runs if run["指标"]["疑点"]]
    tokens = statistics.median(run["指标"]["token"] for run in runs)
    latency = statistics.median(run["耗时ms"] for run in runs)
    costs = sum(run["指标"]["成本"] for run in runs)

    lines = [
        f"# 评测记录：{model}",
        "",
        f"- 时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- provider：{provider}",
        f"- 轮次 × 客户：{total} 份摘要",
        f"- 有引用率：{cited}/{total}",
        f"- 编造日期：{len(issues)}/{total}",
        f"- token 中位：{tokens:.0f}",
        f"- 单次耗时中位：{latency:.0f} ms",
        f"- 成本合计：${costs:.6f}",
        "",
        "## 检索组（不依赖模型）",
        "",
        *[f"- {key}：{value}" for key, value in retrieval.items()],
        "",
        "## 逐份明细",
        "",
        "| 轮次 | 客户 | 引用数 | 可追溯率 | 疑点日期 | token | 耗时ms |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in runs:
        rate = "-" if run["指标"]["可追溯率"] is None else f"{run['指标']['可追溯率']:.2f}"
        bad = ",".join(item["iso"] for item in run["指标"]["疑点"]) or "-"
        lines.append(
            f"| {run['轮次']} | {run['客户']} | {run['指标']['引用数']} | {rate} "
            f"| {bad} | {run['指标']['token']} | {run['耗时ms']} |"
        )
    (folder / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    issue_lines = ["# 问题样例", ""]
    if not issues:
        issue_lines.append("本轮没有抓到编造日期。样本量小不代表不存在。")
    for run in issues:
        for item in run["指标"]["疑点"]:
            issue_lines += [
                f"## 第 {run['轮次']} 轮 · {run['客户']} · {item['iso']}",
                "",
                f"- 正文写法：{' / '.join(item['原文'])}",
                f"- facts：{'、'.join(run['响应']['facts'])}",
                f"- 所在句子：{'；'.join(item['句子']) or '（未能定位到句子）'}",
                "",
            ]
    (folder / "issues.md").write_text("\n".join(issue_lines) + "\n", encoding="utf-8")
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description="摘要/检索/拒答三组评测")
    parser.add_argument("--rounds", type=int, default=3, help="跑几轮，每轮 4 位客户")
    parser.add_argument("--dump", action="store_true", help="把原文与问题句子落盘到 reports/")
    args = parser.parse_args()

    settings = load_config()
    print(f"provider={settings.llm.provider}  model={settings.llm.model}  rounds={args.rounds}")
    if settings.llm.provider == "fake":
        print("⚠ fake provider：摘要指标不是模型表现，只证明脚本能跑通\n")

    app = create_app(settings)
    runs: list[dict] = []
    with TestClient(app) as client:
        for round_index in range(1, args.rounds + 1):
            for name in CUSTOMERS:
                payload, elapsed = _one_call(client, name)
                runs.append(
                    {
                        "轮次": round_index,
                        "客户": name,
                        "响应": payload,
                        "指标": _analyze(payload),
                        "耗时ms": elapsed,
                    }
                )

        retrieval = _retrieval_metrics(app)

    total = len(runs)
    cited = sum(1 for run in runs if run["指标"]["引用数"] > 0)
    issues = sum(1 for run in runs if run["指标"]["疑点"])
    print("— 摘要组 —")
    print(f"有引用率   = {cited}/{total}")
    exact = sum(1 for run in runs if run["指标"]["可追溯率"] == 1.0)
    print(f"可追溯率   = {exact}/{total} 份为 100%（无引用的份不计入）")
    print(f"编造日期   = {issues}/{total}")
    print(f"token 中位 = {statistics.median(r['指标']['token'] for r in runs):.0f}")
    print(f"耗时中位   = {statistics.median(r['耗时ms'] for r in runs):.0f} ms")
    print(f"成本合计   = ${sum(r['指标']['成本'] for r in runs):.6f}")

    print("\n— 检索组（gold.yaml 24 题）—")
    for key, value in retrieval.items():
        print(f"{key:<10} = {value}")

    if args.dump:
        folder = _write_report(runs, retrieval, settings.llm.provider, settings.llm.model)
        print(f"\n证据已落盘：{folder}")
    if settings.llm.provider == "fake":
        print("\n以上摘要指标在 fake 下无意义：换真 provider 重跑，数字会自动变成模型表现。")


if __name__ == "__main__":
    main()
