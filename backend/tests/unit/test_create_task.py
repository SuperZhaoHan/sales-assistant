"""2.3 验收：写工具只在确认后落库，重放只落一行。

关键时刻只有两个：
1. 暂停时库里必须是 0（确认前不能写）；
2. 同一幂等键重放 N 次，库里必须还是 1（幂等不是「尽量」，是硬保证）。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import GraphDeps, build_graph, resume, thread_config
from app.agent.tasks import dedupe_key, make_committer
from app.store.sqlite_store import CustomerStore
from app.tools.create_task import build_create_task_tool
from app.tools.registry import (
    execute,
    non_idempotent_writes,
    register,
    requires_confirmation,
    reset_registry,
)

CREATE_TASK = "帮张伟建个待办，3 天后提醒我打电话确认续保"


@pytest.fixture
def store() -> Iterator[CustomerStore]:
    s = CustomerStore(":memory:").open()
    yield s
    s.close()


class Deps:
    """图依赖：查档与检索是假的，提交走真实的 sqlite 写入。"""

    def __init__(self, store: CustomerStore) -> None:
        self._store = store

    async def lookup_customer(self, name: str) -> str:
        return "张伟，保单 2026-12-05 到期，状态 in_grace"

    async def retrieve_knowledge(self, query: str) -> str:
        return "犹豫期内退保按现金价值计算，需在 15 天内提出"

    def build(self) -> GraphDeps:
        return GraphDeps(
            lookup_customer=self.lookup_customer,
            retrieve_knowledge=self.retrieve_knowledge,
            commit_task=make_committer(self._store),
        )


async def test_no_task_before_confirm(store: CustomerStore) -> None:
    graph = build_graph(Deps(store).build(), checkpointer=InMemorySaver())

    paused = await graph.ainvoke(
        {"task": CREATE_TASK, "customer_name": "张伟"}, thread_config("t-23-pause")
    )

    assert "__interrupt__" in paused
    assert store.count_tasks() == 0, "确认前一行都不许写"


async def test_confirm_writes_exactly_one_task(store: CustomerStore) -> None:
    graph = build_graph(Deps(store).build(), checkpointer=InMemorySaver())
    config = thread_config("t-23-confirm")

    await graph.ainvoke({"task": CREATE_TASK, "customer_name": "张伟"}, config)
    final = await graph.ainvoke(resume(True), config)

    assert final["stage"] == "committed"
    assert store.count_tasks() == 1
    tasks = store.list_tasks("张伟")
    assert tasks[0].id == final["task_id"]
    assert tasks[0].title == "为张伟确认续保"
    assert tasks[0].status == "open"


async def test_reject_writes_nothing(store: CustomerStore) -> None:
    graph = build_graph(Deps(store).build(), checkpointer=InMemorySaver())
    config = thread_config("t-23-reject")

    await graph.ainvoke({"task": CREATE_TASK, "customer_name": "张伟"}, config)
    final = await graph.ainvoke(resume(False), config)

    assert final["stage"] == "aborted"
    assert store.count_tasks() == 0


async def test_replay_three_times_keeps_one_row(store: CustomerStore) -> None:
    """模拟确认请求重放：同一线程、同一参数算三次，只落一行、返回同一个号。"""

    commit = make_committer(store)
    payload: Mapping[str, str] = {
        "thread_id": "t-23-replay",
        "customer_name": "张伟",
        "title": "为张伟确认续保",
    }

    ids = [await commit(payload) for _ in range(3)]

    assert len(set(ids)) == 1, "三次必须返回同一个任务号"
    assert store.count_tasks() == 1


async def test_same_content_different_thread_creates_two_rows(store: CustomerStore) -> None:
    """不同线程是不同的一次跟进，允许各建一条任务——幂等键里必须带 thread_id。"""

    commit = make_committer(store)
    base = {"customer_name": "张伟", "title": "为张伟确认续保"}
    first = await commit({"thread_id": "t-23-a", **base})
    second = await commit({"thread_id": "t-23-b", **base})

    assert first != second
    assert store.count_tasks() == 2


def test_dedupe_key_ignores_argument_order() -> None:
    left = dedupe_key("t-1", "create_task", {"title": "续保", "customer_name": "张伟"})
    right = dedupe_key("t-1", "create_task", {"customer_name": "张伟", "title": "续保"})
    assert left == right


async def test_create_task_tool_is_write_and_idempotent(store: CustomerStore) -> None:
    """工具层：登记成写工具、需要确认；同一 dedupe_key 调两次只落一行。"""

    reset_registry()
    register(build_create_task_tool(store))
    try:
        assert requires_confirmation("create_task") is True
        assert non_idempotent_writes() == ()

        args = {
            "customer_name": "张伟",
            "title": "为张伟确认续保",
            "dedupe_key": dedupe_key("t-23-tool", "create_task", {"title": "为张伟确认续保"}),
        }
        first = await execute("create_task", args)
        second = await execute("create_task", args)

        assert first == second
        assert "tk-" in first
        assert store.count_tasks() == 1
    finally:
        reset_registry()
