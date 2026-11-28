"""2.4 验收：超时默认拒绝（409 + 审计留痕），且库里仍为 0。

判定顺序是本节的考点：先判有没有待确认 → 再判超时 → 最后才恢复。
把顺序倒过来（先恢复再判），「超时」就变成事后记录，副作用已经发生。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.confirm import confirm_task
from app.agent.graph import GraphDeps, build_graph, thread_config
from app.agent.tasks import make_committer
from app.store.sqlite_store import CustomerStore

TASK = "帮张伟建个待办，3 天后提醒我打电话确认续保"
TTL = 300


@pytest.fixture
def store() -> Iterator[CustomerStore]:
    s = CustomerStore(":memory:").open()
    yield s
    s.close()


@pytest.fixture
def graph(store: CustomerStore):
    async def lookup_customer(name: str) -> str:
        return f"{name}，保单 2026-12-05 到期"

    async def retrieve_knowledge(_query: str) -> str:
        return "犹豫期 15 天内退保按现金价值计算"

    deps = GraphDeps(
        lookup_customer=lookup_customer,
        retrieve_knowledge=retrieve_knowledge,
        commit_task=make_committer(store),
    )
    return build_graph(deps, checkpointer=InMemorySaver())


async def start(graph, thread_id: str) -> float:
    paused = await graph.ainvoke({"task": TASK, "customer_name": "张伟"}, thread_config(thread_id))
    assert "__interrupt__" in paused
    snapshot = await graph.aget_state(thread_config(thread_id))
    return datetime.fromisoformat(str(snapshot.created_at)).timestamp()


async def test_confirm_within_ttl_writes_one_task(store: CustomerStore, graph) -> None:
    requested = await start(graph, "t-24-ok")

    outcome = await confirm_task(graph, store, "t-24-ok", True, ttl_seconds=TTL, now=requested + 1)

    assert outcome.status == "confirmed"
    assert outcome.http_status == 200
    assert store.count_tasks() == 1
    statuses = [row["status"] for row in store.list_actions("t-24-ok")]
    assert statuses == ["confirmed"]


async def test_expired_confirm_is_rejected_and_audited(store: CustomerStore, graph) -> None:
    requested = await start(graph, "t-24-late")

    outcome = await confirm_task(
        graph, store, "t-24-late", True, ttl_seconds=TTL, now=requested + TTL + 1
    )

    assert outcome.status == "expired"
    assert outcome.http_status == 409
    assert outcome.code == "CONFIRM_EXPIRED"
    assert store.count_tasks() == 0, "超时后不许有副作用"
    action = store.list_actions("t-24-late")[0]
    assert action["status"] == "expired"
    assert "age=" in str(action["detail"])


async def test_exactly_at_ttl_is_still_allowed(store: CustomerStore, graph) -> None:
    """边界：恰好等于 TTL 不算超时（判定用 >，不是 >=）。"""

    requested = await start(graph, "t-24-edge")

    outcome = await confirm_task(
        graph, store, "t-24-edge", True, ttl_seconds=TTL, now=requested + TTL
    )

    assert outcome.status == "confirmed"
    assert store.count_tasks() == 1


async def test_reject_writes_nothing_and_audits(store: CustomerStore, graph) -> None:
    requested = await start(graph, "t-24-reject")

    outcome = await confirm_task(
        graph, store, "t-24-reject", False, ttl_seconds=TTL, now=requested + 1
    )

    assert outcome.status == "rejected"
    assert outcome.http_status == 200
    assert store.count_tasks() == 0
    assert store.list_actions("t-24-reject")[0]["status"] == "rejected"


async def test_unknown_thread_has_no_pending_confirm(store: CustomerStore, graph) -> None:
    outcome = await confirm_task(graph, store, "t-24-ghost", True, ttl_seconds=TTL)

    assert outcome.status == "missing"
    assert outcome.http_status == 409
    assert outcome.code == "NO_PENDING_CONFIRM"
    assert store.count_actions() == 0


async def test_second_confirm_after_done_does_not_write_again(store: CustomerStore, graph) -> None:
    requested = await start(graph, "t-24-twice")
    first = await confirm_task(graph, store, "t-24-twice", True, ttl_seconds=TTL, now=requested + 1)
    second = await confirm_task(
        graph, store, "t-24-twice", True, ttl_seconds=TTL, now=requested + 2
    )

    assert first.status == "confirmed"
    assert second.status == "missing", "已经处理过的线程没有待确认项"
    assert store.count_tasks() == 1
