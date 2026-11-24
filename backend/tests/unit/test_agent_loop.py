"""阶段 0.2 验收：假模型能跑完三步，产出一条结构化结果。

用的是仓库里已有的 ``FakeLLMProvider``（``app/llm/fake.py``），
所以这条链路是「真的走了 LLM 请求—响应」，只是不联网、不花钱。
"""

from __future__ import annotations

from app.agent.loop import run_agent
from app.agent.planner import FakeLLMPlanner
from app.agent.state import Intent, StopReason
from app.llm.fake import FakeLLMProvider
from tests.factories.agent import make_limits
from tests.fakes.tool_executor import RecordingToolExecutor

CUSTOMER = "张伟，保单 2026-12-05 到期，状态 in_grace"
KNOWLEDGE = "犹豫期内退保按现金价值计算，且需在 15 天内提出"


async def test_fake_model_runs_three_steps_and_returns_answer() -> None:
    provider = FakeLLMProvider()
    planner = FakeLLMPlanner(provider)
    tools = RecordingToolExecutor({"customer_profile": CUSTOMER, "knowledge_search": KNOWLEDGE})

    result = await run_agent(
        "帮我看看张总现在什么阶段，再查下犹豫期退保怎么算",
        planner=planner,
        tools=tools,
        limits=make_limits(max_steps=6, max_tokens=3000),
    )

    assert result.stop_reason is StopReason.DONE
    assert len(result.steps) == 3, "查客户 → 查知识 → 出答复，正好三步"
    assert [step["action"] for step in result.steps] == [
        "customer_profile",
        "knowledge_search",
        None,
    ]
    assert tools.calls[0][0] == "customer_profile"

    assert result.answer is not None
    assert "fake-model" in result.answer
    assert "in_grace" in result.answer, "第一次查到的客户事实必须还在，不能被第二次观察冲掉"
    assert "现金价值" in result.answer, "第二次查到的知识也必须进提示词"

    assert result.tokens == sum(step["tokens"] for step in result.steps)
    assert result.tokens > 0
    assert result.cost_usd > 0


async def test_chitchat_skips_tools_and_stops_in_one_step() -> None:
    missing = FakeLLMProvider()
    tools = RecordingToolExecutor()

    result = await run_agent("在吗", planner=FakeLLMPlanner(missing), tools=tools)

    assert result.stop_reason is StopReason.DONE
    assert len(result.steps) == 1
    assert result.steps[0]["action"] is None
    assert tools.calls == []


def test_intent_is_classified_before_the_loop_runs() -> None:
    from app.agent.intent import classify_intent

    assert classify_intent("帮我看看张总现在什么阶段") is Intent.ASK_CUSTOMER
