"""2.2 契约：状态能扛进程重启、索引幂等、索引丢了能从 checkpoint 重建。

只在「有 psycopg + 有真库」时跑，CI 里自动 skip。本地跑法：

    docker run -d --name sales-pg -e POSTGRES_PASSWORD=postgres \
        -p 5482:5432 pgvector/pgvector:pg16
    PG_TEST_DSN=postgresql://postgres:postgres@localhost:5482/postgres \
        .venv/bin/python -m pytest tests/integration/test_checkpoint_postgres.py -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping

import pytest

pytest.importorskip("psycopg", reason="checkpointer 需要 psycopg")
pytest.importorskip("langgraph.checkpoint.postgres", reason="需要 langgraph-checkpoint-postgres")

from psycopg import AsyncConnection  # noqa: E402

from app.agent.graph import GraphDeps, build_graph, resume, thread_config  # noqa: E402
from app.agent.persistence import (  # noqa: E402
    ThreadIndex,
    open_checkpointer,
    rebuild_threads,
)
from app.agent.state import TaskStage  # noqa: E402

DSN = os.getenv("PG_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="未设置 PG_TEST_DSN，跳过真库用例")

CREATE_TASK = "帮张伟建个待办，3 天后提醒我打电话确认续保"
QUERY = "帮我看看张伟现在什么阶段，犹豫期怎么算"


class RecordingDeps:
    """记账版依赖：只数「提交了几次」，不写任何地方。"""

    def __init__(self) -> None:
        self.commits: list[Mapping[str, str]] = []

    async def lookup_customer(self, name: str) -> str:
        return "张伟，保单 2026-12-05 到期，状态 in_grace"

    async def retrieve_knowledge(self, query: str) -> str:
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


@pytest.fixture
async def index() -> AsyncIterator[ThreadIndex]:
    """一条独立的业务索引连接；用例结束把本用例造的行删掉，不污染别的数据。"""

    conn = await AsyncConnection.connect(DSN, autocommit=True)
    idx = ThreadIndex(conn)
    await idx.ensure_schema()
    yield idx
    await idx.clear_prefix("t-2.2")
    # checkpoint 三张表一起清，否则下一轮 list 会把上一轮的线程算进来。
    for table in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
        async with conn.cursor() as cur:
            await cur.execute(
                f"DELETE FROM {table} WHERE thread_id LIKE %(prefix)s",  # noqa: S608
                {"prefix": "t-2.2%"},
            )
    await conn.close()


async def test_state_survives_restart(index: ThreadIndex) -> None:
    """核心证据：关掉连接池（等价于进程退出），新 saver 接着把任务写完。"""

    thread_id = "t-2.2-restart"
    saver1, pool1 = await open_checkpointer(DSN)
    graph1 = build_graph(RecordingDeps().deps(), checkpointer=saver1)

    paused = await graph1.ainvoke(
        {"task": CREATE_TASK, "customer_name": "张伟"}, thread_config(thread_id)
    )
    assert "__interrupt__" in paused
    await index.remember(
        thread_id,
        customer_name="张伟",
        status=TaskStage.AWAITING_CONFIRM.value,
        last_node="await_confirm",
    )
    await pool1.close()

    # 换一套连接池与图实例：内存里一切都没了，只剩库里的 checkpoints。
    saver2, pool2 = await open_checkpointer(DSN)
    recorder = RecordingDeps()
    graph2 = build_graph(recorder.deps(), checkpointer=saver2)
    final = await graph2.ainvoke(resume(True), thread_config(thread_id))
    await pool2.close()

    assert final["stage"] == TaskStage.COMMITTED.value
    assert final["task_id"] == "task-1"
    assert len(recorder.commits) == 1, "重启后恢复，写仍然只有一次"
    assert final["trace"] == [
        "classify_intent",
        "fetch_customer",
        "retrieve_knowledge",
        "draft_advice",
        "await_confirm",
        "commit_task",
    ], "已完成的前四个节点没有重跑，轨迹是接上去的"


async def test_index_upsert_is_idempotent(index: ThreadIndex) -> None:
    """同一线程写五次只有一行：索引是幂等投影，不是流水账。"""

    thread_id = "t-2.2-idem"
    for _ in range(5):
        await index.remember(
            thread_id,
            customer_name="张伟",
            status=TaskStage.DRAFT.value,
            last_node="draft_advice",
            ttl_seconds=300,
        )

    row = await index.get(thread_id)
    assert row is not None
    assert row["status"] == TaskStage.DRAFT.value
    assert await index.count_prefix(thread_id) == 1


async def test_index_has_no_state_column(index: ThreadIndex) -> None:
    """结构级防线：agent_threads 里不许出现 state 字段，想双写也没地方放。"""

    columns = await index.columns()
    assert "state" not in columns
    assert set(columns) == {
        "thread_id",
        "customer_name",
        "status",
        "last_node",
        "expires_at",
        "updated_at",
    }


async def test_rebuild_restores_index_from_checkpoints(index: ThreadIndex) -> None:
    """清空索引后重建：两条线程都能回来，暂停的那条要能认出「在等人」。"""

    saver, pool = await open_checkpointer(DSN)
    graph = build_graph(RecordingDeps().deps(), checkpointer=saver)

    done = "t-2.2-done"
    paused = "t-2.2-paused"
    await graph.ainvoke({"task": QUERY, "customer_name": "张伟"}, thread_config(done))
    await graph.ainvoke({"task": CREATE_TASK, "customer_name": "张伟"}, thread_config(paused))

    await index.clear_prefix("t-2.2")
    assert await index.count_prefix("t-2.2") == 0

    restored = await rebuild_threads(saver, index)
    await pool.close()

    assert restored >= 2
    row_done = await index.get(done)
    row_paused = await index.get(paused)
    assert row_done is not None and row_paused is not None
    assert row_done["last_node"] == "draft_advice"
    assert row_paused["status"] == TaskStage.AWAITING_CONFIRM.value
    assert row_paused["last_node"] == "await_confirm"
    assert row_paused["customer_name"] == "张伟"
