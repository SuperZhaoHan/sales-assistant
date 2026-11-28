"""2.1 验收：显式状态图能跑通、同输入两次节点序列一致、暂停后能接着写或不写。

这是「可失败用例」的正脸：不接真模型、不连库，全部依赖是记账用的假函数，
所以任何一条断言挂掉，都能立刻定位到「图的路由错了」而不是「模型今天不听话」。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import GraphDeps, build_graph, resume, thread_config
from app.agent.state import TaskStage

TASK_QUERY = "帮我看看张伟现在什么阶段，续保的犹豫期怎么算"
TASK_CREATE = "帮张伟建个待办，3 天后提醒我打电话确认续保"


class RecordingDeps:
    """把三个依赖都记下来：调了几次、带了什么参数。"""

    def __init__(self) -> None:
        self.customer_calls: list[str] = []
        self.knowledge_calls: list[str] = []
        self.commits: list[Mapping[str, str]] = []

    async def lookup_customer(self, name: str) -> str:
        self.customer_calls.append(name)
        return "张伟，保单 2026-12-05 到期，状态 in_grace"

    async def retrieve_knowledge(self, query: str) -> str:
        self.knowledge_calls.append(query)
        return "犹豫期内退保按现金价值计算，需在 15 天内提出"

    async def commit_task(self, payload: Mapping[str, str]) -> str:
        self.commits.append(dict(payload))
        return f"task-{len(self.commits)}"

    def deps(self) -> GraphDeps:
        return GraphDeps(
            lookup_customer=self.lookup_customer,
            retrieve_knowledge=self.retrieve_knowledge,
            commit_task=self.commit_task,
        )


def make_graph(recorder: RecordingDeps) -> Any:
    return build_graph(recorder.deps(), checkpointer=InMemorySaver())


async def test_same_input_twice_same_node_sequence() -> None:
    """验收线：同输入跑两遍，节点序列必须逐字相同。"""

    first = make_graph(RecordingDeps())
    second = make_graph(RecordingDeps())

    state = {"task": TASK_QUERY, "customer_name": "张伟"}
    a = await first.ainvoke(state, thread_config("t-a"))
    b = await second.ainvoke(state, thread_config("t-b"))

    expected = ["classify_intent", "fetch_customer", "retrieve_knowledge", "draft_advice"]
    assert a["trace"] == expected
    assert b["trace"] == expected


async def test_create_task_pauses_for_confirmation() -> None:
    """建任务必须停在 await_confirm，且暂停时一次写都没发生。"""

    recorder = RecordingDeps()
    graph = make_graph(recorder)

    paused = await graph.ainvoke(
        {"task": TASK_CREATE, "customer_name": "张伟"}, thread_config("t-confirm")
    )

    assert "__interrupt__" in paused
    payload = paused["__interrupt__"][0].value
    assert "确认创建任务" in payload["question"]
    assert recorder.commits == [], "确认之前不许写库"
    assert paused["stage"] == TaskStage.DRAFT.value, "暂停时节点尚未返回，阶段还是 draft"


async def test_resume_true_commits_once() -> None:
    """确认后从断点续跑：只写一次，终态 committed，轨迹补齐到六个节点。"""

    recorder = RecordingDeps()
    graph = make_graph(recorder)
    config = thread_config("t-approve")

    await graph.ainvoke({"task": TASK_CREATE, "customer_name": "张伟"}, config)
    final = await graph.ainvoke(resume(True), config)

    assert final["stage"] == TaskStage.COMMITTED.value
    assert final["task_id"] == "task-1"
    assert len(recorder.commits) == 1
    assert final["trace"][-2:] == ["await_confirm", "commit_task"]


async def test_resume_false_aborts_without_write() -> None:
    """拒绝也走同一个终态节点，但一行数据都不写。"""

    recorder = RecordingDeps()
    graph = make_graph(recorder)
    config = thread_config("t-reject")

    await graph.ainvoke({"task": TASK_CREATE, "customer_name": "张伟"}, config)
    final = await graph.ainvoke(resume(False), config)

    assert final["stage"] == TaskStage.ABORTED.value
    assert recorder.commits == []


async def test_chitchat_skips_every_lookup() -> None:
    """闲聊不查档、不检索——条件边要真的能剪掉无用分支。"""

    recorder = RecordingDeps()
    graph = make_graph(recorder)

    result = await graph.ainvoke({"task": "你好"}, thread_config("t-chat"))

    assert result["trace"] == ["classify_intent"]
    assert recorder.customer_calls == []
    assert recorder.knowledge_calls == []


async def test_no_customer_name_skips_fetch() -> None:
    """没给客户名时跳过查询节点，但知识检索照做。"""

    recorder = RecordingDeps()
    graph = make_graph(recorder)

    result = await graph.ainvoke({"task": TASK_QUERY}, thread_config("t-no-name"))

    assert result["trace"] == ["classify_intent", "retrieve_knowledge", "draft_advice"]
    assert recorder.customer_calls == []
    assert recorder.knowledge_calls == [TASK_QUERY]
