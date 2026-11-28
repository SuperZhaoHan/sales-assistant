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

    # 超时要给线程收尾：interrupt 必须清空，否则会永远挂在库里、列表页一直「待确认」
    snapshot = await graph.aget_state(thread_config("t-24-late"))
    assert snapshot.interrupts == ()
    assert snapshot.values["stage"] == "aborted", "超时按拒绝走，终态是 aborted"


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


async def test_second_confirm_replays_first_result(store: CustomerStore, graph) -> None:
    """2.5：同一确认重放，返回原任务号，不是 409、不是新任务。"""

    requested = await start(graph, "t-24-twice")
    first = await confirm_task(graph, store, "t-24-twice", True, ttl_seconds=TTL, now=requested + 1)
    second = await confirm_task(
        graph, store, "t-24-twice", True, ttl_seconds=TTL, now=requested + 2
    )

    assert first.status == "confirmed"
    assert second.status == "confirmed", "重放必须返回成功，而不是「没有待确认」"
    assert second.task_id == first.task_id
    assert second.http_status == 200
    assert store.count_tasks() == 1
    assert len(store.list_actions("t-24-twice")) == 1, "重放不该多写一行审计"


async def test_replay_three_times_keeps_one_row(store: CustomerStore, graph) -> None:
    requested = await start(graph, "t-25-replay")

    ids = [
        (
            await confirm_task(
                graph, store, "t-25-replay", True, ttl_seconds=TTL, now=requested + 1
            )
        ).task_id
        for _ in range(3)
    ]

    assert len(set(ids)) == 1
    assert store.count_tasks() == 1
    assert len(store.list_actions("t-25-replay")) == 1


async def test_expired_replay_returns_expired_not_missing(store: CustomerStore, graph) -> None:
    """超时后再点一次：仍然是 409 expired（原结果），不是 409 missing（像没这回事）。"""

    requested = await start(graph, "t-25-late")
    first = await confirm_task(
        graph, store, "t-25-late", True, ttl_seconds=TTL, now=requested + TTL + 1
    )
    second = await confirm_task(
        graph, store, "t-25-late", True, ttl_seconds=TTL, now=requested + TTL + 2
    )

    assert first.status == "expired"
    assert second.status == "expired"
    assert second.code == "CONFIRM_EXPIRED"
    assert store.count_tasks() == 0


async def test_opposite_decision_is_a_different_request(store: CustomerStore, graph) -> None:
    """确认之后再点「拒绝」不是重放：幂等键里带了决定，两个请求不同。"""

    requested = await start(graph, "t-25-opposite")
    first = await confirm_task(
        graph, store, "t-25-opposite", True, ttl_seconds=TTL, now=requested + 1
    )
    second = await confirm_task(
        graph, store, "t-25-opposite", False, ttl_seconds=TTL, now=requested + 2
    )

    assert first.status == "confirmed"
    assert second.status == "missing"
    assert store.count_tasks() == 1
