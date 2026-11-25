"""最小循环：判停机 → 问规划器 → 执行动作 → 记账 → 再来一次。

三条设计约束：

1. **不在这里调模型**。规划器是注入的 ``Planner``，所以每个停机原因都能用一个
   照剧本演的假规划器在毫秒级复现，不需要真模型、不需要网络。
2. **不在这里判业务**。工具是注入的 ``ToolExecutor``，循环只负责「叫它执行」。
3. **每一轮都先判停机**。判在调用规划器之前，所以 ``max_steps=2`` 时规划器
   一定只被调用两次——这一点有断言，不靠「大概不会多调」。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.intent import classify_intent
from app.agent.state import AgentResult, AgentState, Step, StopReason
from app.agent.stop import Limits, check_stop

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass
class Decision:
    """规划器给出的下一步。四种组合都有明确含义：

    - 只有 ``action``：去调工具；
    - 只有 ``answer``：可以收尾了；
    - 两者都有：先记下答复，再执行动作（少见，但允许）；
    - 两者都空：记一步空转，靠 ``max_steps`` 兜住，不会死循环。
    """

    thought: str = ""
    action: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Planner(Protocol):
    async def __call__(self, state: AgentState) -> Decision: ...


MISSING_BY_REASON: dict[StopReason, str] = {
    StopReason.DONE: "",
    StopReason.MAX_STEPS: "步数用尽，还没产出最终答复",
    StopReason.BUDGET_EXCEEDED: "token 或费用预算用尽，任务未完成",
    StopReason.TIMEOUT: "超过时限，任务未完成",
    StopReason.REFUSED: "被拒绝或缺少执行能力，任务未完成",
}


async def run_agent(
    task: str,
    *,
    planner: Planner,
    limits: Limits | None = None,
    tools: ToolExecutor | None = None,
    intents: Mapping[str, tuple[str, ...]] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> AgentResult:
    """跑一次任务，永远返回 ``AgentResult``，不抛「跑不完」这类异常。

    缺工具执行器时不抛异常，而是把 ``refusal`` 置上、下一轮按 ``refused`` 收尾：
    半成品 + 缺什么，比 500 更有用。

    ``intents`` 是 domain pack 的意图词表，由调用方注入——循环本身不知道行业。
    """

    limits = limits or Limits()
    state = AgentState(task=task, intent=classify_intent(task, intents))
    deadline = now() + limits.timeout_seconds

    while True:
        reason = check_stop(state, limits, now=now(), deadline=deadline)
        if reason is not None:
            return _build_result(state, reason)

        decision = await planner(state)

        observation: str | None = None
        if decision.action is not None:
            if tools is None:
                state.refusal = f"没有注册工具执行器，无法执行 {decision.action}"
            else:
                observation = await tools(decision.action, decision.args)

        if decision.answer is not None:
            state.answer = decision.answer

        state.record(
            Step(
                index=len(state.steps),
                thought=decision.thought,
                action=decision.action,
                args=dict(decision.args),
                observation=observation,
                input_tokens=decision.input_tokens,
                output_tokens=decision.output_tokens,
                cost_usd=decision.cost_usd,
            )
        )


def _build_result(state: AgentState, reason: StopReason) -> AgentResult:
    """把内部状态压成对外形状。``missing`` 只说「还差什么」，不猜原因。"""

    missing: list[str] = []
    if reason is not StopReason.DONE:
        missing.append(MISSING_BY_REASON[reason])
    if not state.steps and reason is not StopReason.DONE:
        missing.append("一步都没走成")

    return AgentResult(
        stop_reason=reason,
        answer=state.answer,
        steps=[
            {
                "index": step.index,
                "thought": step.thought,
                "action": step.action,
                "args": step.args,
                "observation": step.observation,
                "tokens": step.tokens,
            }
            for step in state.steps
        ],
        missing=missing,
        tokens=state.tokens_used,
        cost_usd=state.cost_usd,
    )
