"""阶段 0 的实测脚本：把五种停机情形各跑一遍，打印真实数字。

跑法（在 backend 目录）：

    .venv/bin/python scripts/demo_agent_loop.py

不打这行的话，README 里「五个停机原因」就只是说法；跑完才有数。
"""

from __future__ import annotations

import asyncio
import json

from app.agent.loop import Decision, run_agent
from app.agent.planner import ConstantPlanner, FakeLLMPlanner, ScriptedPlanner
from app.agent.state import StopReason
from app.agent.stop import Limits
from app.llm.fake import FakeLLMProvider

CUSTOMER = "张伟，保单 2026-12-05 到期，状态 in_grace"
KNOWLEDGE = "犹豫期内退保按现金价值计算，且需在 15 天内提出"


class Tools:
    """极简执行器：按工具名返回固定观察，顺便记调用次数。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, name: str, args: dict[str, object]) -> str:
        self.calls.append(name)
        return {"customer_profile": CUSTOMER, "knowledge_search": KNOWLEDGE}.get(
            name, f"<{name}> 无数据"
        )


async def main() -> int:
    rows: list[tuple[str, str, int, int, float]] = []

    # 1）正常跑完：假模型三步
    tools = Tools()
    result = await run_agent(
        "帮我看看张总现在什么阶段，再查下犹豫期退保怎么算",
        planner=FakeLLMPlanner(FakeLLMProvider()),
        tools=tools,
        limits=Limits(max_steps=6, max_tokens=3000),
    )
    rows.append(("正常完成", result.stop_reason.value, len(result.steps), result.tokens, result.cost_usd))

    # 2）步数用尽
    result = await run_agent(
        "查客户",
        planner=ConstantPlanner(Decision(action="customer_profile")),
        tools=Tools(),
        limits=Limits(max_steps=2),
    )
    rows.append(("步数用尽", result.stop_reason.value, len(result.steps), result.tokens, result.cost_usd))

    # 3）预算用尽
    result = await run_agent(
        "查客户",
        planner=ScriptedPlanner(
            [
                Decision(action="customer_profile", input_tokens=40, output_tokens=20),
                Decision(action="customer_profile", input_tokens=40, output_tokens=20),
                Decision(answer="不该走到这一步"),
            ]
        ),
        tools=Tools(),
        limits=Limits(max_steps=10, max_tokens=100),
    )
    rows.append(
        ("预算用尽", result.stop_reason.value, len(result.steps), result.tokens, result.cost_usd)
    )

    # 4）超时
    ticks = iter([0.0, 0.0, 1000.0])
    result = await run_agent(
        "查客户",
        planner=ConstantPlanner(Decision(action="customer_profile")),
        tools=Tools(),
        limits=Limits(max_steps=10, timeout_seconds=30),
        now=lambda: next(ticks),
    )
    rows.append(("超时", result.stop_reason.value, len(result.steps), result.tokens, result.cost_usd))

    # 5）拒绝：没有工具执行器
    result = await run_agent(
        "帮我建个待办",
        planner=ScriptedPlanner([Decision(action="create_task")]),
        tools=None,
    )
    rows.append(
        ("拒绝执行", result.stop_reason.value, len(result.steps), result.tokens, result.cost_usd)
    )

    # 表 + 一个半成品响应体示例
    print(f"{'场景':<10}{'stop_reason':<16}{'步数':<6}{'token':<8}{'费用USD':<10}")
    for name, reason, steps, tokens, cost in rows:
        print(f"{name:<10}{reason:<16}{steps:<6}{tokens:<8}{cost:<10.6f}")

    partial = await run_agent(
        "查客户",
        planner=ConstantPlanner(Decision(action="customer_profile")),
        tools=Tools(),
        limits=Limits(max_steps=1),
    )
    print("\n半成品响应体（max_steps=1）：")
    print(json.dumps(partial.to_dict(), ensure_ascii=False, indent=2))

    expected = {
        StopReason.DONE.value,
        StopReason.MAX_STEPS.value,
        StopReason.BUDGET_EXCEEDED.value,
        StopReason.TIMEOUT.value,
        StopReason.REFUSED.value,
    }
    print("\n五种停机原因全部覆盖:", {reason for _, reason, *_ in rows} == expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
