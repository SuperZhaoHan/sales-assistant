"""2.7 撤销任务：逻辑删除 + 复用同一条确认链路。

考点有三个：
1. 「删」真的不删行——行还在、只是 ``status=deleted`` 且有 ``deleted_at``；
2. 重复删不报错、也不产生第二条副作用（UPDATE 影响 0 行）；
3. 确认之前不许动，确认之后才动——和 2.3 的 create 是同一条规矩。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.confirm import confirm_task
from app.agent.graph import GraphDeps, build_graph, thread_config
from app.agent.intent import classify_intent
from app.agent.state import Intent
from app.core.errors import BusinessRuleError, ValidationError
from app.store.sqlite_store import CustomerStore
from app.tools.delete_task import DeleteTaskArgs, build_delete_task_tool

TTL = 300
DELETE_TEXT = "把张伟那条待办删了"


@pytest.fixture
def store() -> Iterator[CustomerStore]:
    s = CustomerStore(":memory:").open()
    yield s
    s.close()


def seed_task(store: CustomerStore, key: str = "seed-delete-1") -> str:
    return store.add_task(customer_name="张伟", title="3 天后回访", dedupe_key=key).id


@pytest.fixture
def graph(store: CustomerStore):
    async def lookup_customer(name: str) -> str:
        return f"{name}，保单 2026-12-05 到期"

    async def retrieve_knowledge(_query: str) -> str:
        return "犹豫期 15 天内退保按现金价值计算"

    async def commit_task(_payload: dict[str, str]) -> str:
        raise AssertionError("删除路径不该调用创建任务")

    async def find_task(customer_name: str) -> str | None:
        task = store.find_open_task(customer_name)
        return task.id if task is not None else None

    async def delete_task(task_id: str) -> None:
        store.close_task(task_id)

    deps = GraphDeps(
        lookup_customer=lookup_customer,
        retrieve_knowledge=retrieve_knowledge,
        commit_task=commit_task,
        find_task=find_task,
        delete_task=delete_task,
    )
    return build_graph(deps, checkpointer=InMemorySaver())


def test_classify_routes_delete_before_create() -> None:
    # 「不用提醒我了」里含「提醒我」：删除词必须排在创建词前面，否则会被当成"再建一个"
    assert classify_intent("不用提醒我了") is Intent.DELETE_TASK
    assert classify_intent(DELETE_TEXT) is Intent.DELETE_TASK
    assert classify_intent("帮我记一下") is Intent.CREATE_TASK


def test_soft_delete_keeps_row(store: CustomerStore) -> None:
    task_id = seed_task(store)

    deleted = store.close_task(task_id)

    assert deleted is not None
    assert deleted.status == "deleted"
    assert deleted.deleted_at is not None
    assert store.count_tasks() == 0, "默认口径不把已撤的算在内"
    assert store.count_tasks(include_deleted=True) == 1, "行还在，逻辑删除不是物理删除"
    assert store.list_tasks(include_deleted=True)[0].id == task_id


def test_delete_twice_is_idempotent(store: CustomerStore) -> None:
    task_id = seed_task(store)

    assert store.close_task(task_id) is not None
    assert store.close_task(task_id) is None, "第二次 UPDATE 影响 0 行，语义是「已经删了」"
    assert store.count_tasks(include_deleted=True) == 1


def test_find_open_task_skips_deleted(store: CustomerStore) -> None:
    deleted_id = seed_task(store, "k-old")
    store.close_task(deleted_id)
    fresh_id = seed_task(store, "k-new")

    found = store.find_open_task("张伟")

    assert found is not None and found.id == fresh_id


def test_tool_reports_missing_and_already_deleted(store: CustomerStore) -> None:
    tool = build_delete_task_tool(store)
    task_id = seed_task(store)

    args = DeleteTaskArgs(task_id=task_id, dedupe_key="d" * 16)

    assert "已撤销" in asyncio.run(tool.handler(args))
    assert "已是撤销状态" in asyncio.run(tool.handler(args))
    with pytest.raises(ValidationError):
        asyncio.run(tool.handler(DeleteTaskArgs(task_id="tk-ghost", dedupe_key="d" * 16)))


@pytest.mark.parametrize("approved", [False, True])
async def test_confirm_gates_delete(store: CustomerStore, graph, approved: bool) -> None:
    task_id = seed_task(store)
    paused = await graph.ainvoke(
        {"task": DELETE_TEXT, "customer_name": "张伟"}, thread_config("t-27-del")
    )
    assert "__interrupt__" in paused, "删除是写操作，必须先停一下"
    requested = datetime.fromisoformat(
        str((await graph.aget_state(thread_config("t-27-del"))).created_at)
    ).timestamp()

    outcome = await confirm_task(
        graph, store, "t-27-del", approved, ttl_seconds=TTL, now=requested + 1
    )

    kept = store.get_task(task_id)
    assert outcome.status == ("confirmed" if approved else "rejected")
    # 只有真的删了才回任务号；拒绝是"什么都没发生"，回号会让人以为删掉了
    assert outcome.task_id == (task_id if approved else None)
    assert kept is not None
    assert kept.status == ("deleted" if approved else "open")


async def test_delete_without_open_task_is_a_business_error(store: CustomerStore, graph) -> None:
    with pytest.raises(BusinessRuleError) as excinfo:
        await graph.ainvoke(
            {"task": DELETE_TEXT, "customer_name": "张伟"}, thread_config("t-27-none")
        )

    assert excinfo.value.details["code"] == "NO_OPEN_TASK"
