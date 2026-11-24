"""停机条件的单元测试：五个原因，五条用例，每条断言精确的 ``stop_reason``。

这组用例的设计前提是「每一种停法都能在毫秒内复现」：
- 步数：``max_steps=2`` + 永远给动作的规划器；
- 预算：每步 60 token，上限 100；
- 超时：注入假时钟，第二次检查时直接跳到 1000 秒；
- 拒绝：不给工具执行器；
- 完成：规划器直接给答复。

如果哪天循环里的判据被删掉，这些用例会分别变成死循环、超支、超时、
带病返回或断言失败——不是「测试变红」这么笼统，而是能指出少了哪道闸。
"""

from __future__ import annotations

import json

import pytest

from app.agent.loop import run_agent
from app.agent.planner import ConstantPlanner, ScriptedPlanner
from app.agent.state import StopReason
from app.agent.stop import Limits
from app.core.errors import ValidationError
from tests.factories.agent import make_decision, make_limits
from tests.fakes.tool_executor import RecordingToolExecutor


async def test_done_returns_answer_and_records_one_step() -> None:
    planner = ScriptedPlanner([make_decision(answer="建议今天先发一条到期提醒")])

    result = await run_agent("张总的保单到期了怎么办", planner=planner)

    assert result.stop_reason is StopReason.DONE
    assert result.ok is True
    assert result.answer == "建议今天先发一条到期提醒"
    assert len(result.steps) == 1
    assert result.missing == []


async def test_max_steps_stops_before_calling_planner_again() -> None:
    limits = make_limits(max_steps=2)
    planner = ConstantPlanner(make_decision(action="customer_profile"))
    tools = RecordingToolExecutor({"customer_profile": "张伟 / in_grace"})

    result = await run_agent("查一下张总的保单", planner=planner, tools=tools, limits=limits)

    assert result.stop_reason is StopReason.MAX_STEPS
    assert len(result.steps) == 2
    assert planner.calls == 2, "停机判据必须在调用规划器之前，否则会多跑一步"
    assert len(tools.calls) == 2
    assert result.missing == ["步数用尽，还没产出最终答复"]


async def test_budget_exceeded_stops_after_overspending_step() -> None:
    limits = make_limits(max_steps=10, max_tokens=100)
    planner = ScriptedPlanner(
        [
            make_decision(action="customer_profile", input_tokens=40, output_tokens=20),
            make_decision(action="customer_profile", input_tokens=40, output_tokens=20),
            make_decision(answer="不该走到这一步", input_tokens=40, output_tokens=20),
        ]
    )
    tools = RecordingToolExecutor()

    result = await run_agent("查客户", planner=planner, tools=tools, limits=limits)

    assert result.stop_reason is StopReason.BUDGET_EXCEEDED
    assert len(result.steps) == 2
    assert result.tokens == 120, "超支的那一步也算钱，不能被丢掉"
    assert planner.calls == 2


async def test_timeout_interrupts_a_running_loop() -> None:
    ticks = iter([0.0, 0.0, 1000.0])
    planner = ConstantPlanner(make_decision(action="customer_profile"))
    tools = RecordingToolExecutor()

    result = await run_agent(
        "查客户",
        planner=planner,
        tools=tools,
        limits=make_limits(max_steps=10, timeout_seconds=30),
        now=lambda: next(ticks),
    )

    assert result.stop_reason is StopReason.TIMEOUT
    assert len(result.steps) == 1, "第一轮没事，第二轮才发现超时"


async def test_refused_when_no_tool_executor_is_registered() -> None:
    planner = ScriptedPlanner([make_decision(action="create_task")])

    result = await run_agent("帮我建个待办", planner=planner, tools=None)

    assert result.stop_reason is StopReason.REFUSED
    assert len(result.steps) == 1
    assert result.answer is None
    assert result.missing


async def test_empty_decision_cannot_loop_forever() -> None:
    """两种都空的决策也会记一步，所以最坏情况一定停在 max_steps。"""

    planner = ConstantPlanner(make_decision())
    result = await run_agent("随便聊聊", planner=planner, limits=make_limits(max_steps=3))

    assert result.stop_reason is StopReason.MAX_STEPS
    assert len(result.steps) == 3


@pytest.mark.parametrize("max_steps", [0, -1])
def test_limits_reject_non_positive_values(max_steps: int) -> None:
    with pytest.raises(ValidationError):
        Limits(max_steps=max_steps)


async def test_partial_result_is_json_serializable_with_missing() -> None:
    """阶段 0.4：半成品结果要能直接塞进响应体，不能让序列化抛 500。"""

    planner = ConstantPlanner(make_decision(action="customer_profile"))
    result = await run_agent(
        "查客户",
        planner=planner,
        tools=RecordingToolExecutor(),
        limits=make_limits(max_steps=1),
    )

    payload = result.to_dict()
    json.dumps(payload, ensure_ascii=False, default=str)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "max_steps"
    assert payload["partial"]["step_count"] == 1
    assert payload["partial"]["steps"][0]["action"] == "customer_profile"
    assert payload["missing"] == ["步数用尽，还没产出最终答复"]
