"""2.2 计时：一次完整任务写几个 checkpoint、跨重启恢复要多久。

只跑本地真库，用来给报告和面试提供数字；不参与 CI。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping

from langgraph.checkpoint.memory import InMemorySaver
from psycopg import AsyncConnection

from app.agent.graph import GraphDeps, build_graph, resume, thread_config
from app.agent.persistence import ThreadIndex, open_checkpointer

DSN = os.getenv("AGENT_CHECKPOINT_DSN", "postgresql://postgres:postgres@localhost:5482/postgres")
CREATE_TASK = "帮张伟建个待办，3 天后提醒我打电话确认续保"
QUERY = "帮我看看张伟现在什么阶段，犹豫期怎么算"


class Deps:
    async def lookup_customer(self, name: str) -> str:
        return "张伟，保单 2026-12-05 到期，状态 in_grace"

    async def retrieve_knowledge(self, query: str) -> str:
        return "犹豫期内退保按现金价值计算，需在 15 天内提出"

    async def commit_task(self, payload: Mapping[str, str]) -> str:
        return "task-1"

    def build(self) -> GraphDeps:
        return GraphDeps(self.lookup_customer, self.retrieve_knowledge, self.commit_task)


async def _count(conn: AsyncConnection, table: str, thread_id: str) -> int:
    async with conn.cursor() as cur:
        await cur.execute(f"SELECT count(*) FROM {table} WHERE thread_id = %(t)s", {"t": thread_id})
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def main() -> None:
    deps = Deps().build()

    # A. 内存版：pause + resume 的基线
    mem = build_graph(deps, checkpointer=InMemorySaver())
    t0 = time.perf_counter()
    await mem.ainvoke({"task": CREATE_TASK, "customer_name": "张伟"}, thread_config("m-1"))
    await mem.ainvoke(resume(True), thread_config("m-1"))
    mem_ms = (time.perf_counter() - t0) * 1000

    # B. 真库版：暂停 → 关池（等价进程退出）→ 重开 → 恢复
    saver, pool = await open_checkpointer(DSN)
    graph = build_graph(deps, checkpointer=saver)
    conn = await AsyncConnection.connect(DSN, autocommit=True)
    index = ThreadIndex(conn)
    await index.ensure_schema()

    tid = "m-2.2-pg"
    await graph.ainvoke({"task": CREATE_TASK, "customer_name": "张伟"}, thread_config(tid))
    counts = {
        "checkpoints": await _count(conn, "checkpoints", tid),
        "checkpoint_blobs": await _count(conn, "checkpoint_blobs", tid),
        "checkpoint_writes": await _count(conn, "checkpoint_writes", tid),
    }
    await index.remember(
        tid, customer_name="张伟", status="awaiting_confirm", last_node="await_confirm"
    )
    await pool.close()

    t1 = time.perf_counter()
    saver2, pool2 = await open_checkpointer(DSN)
    graph2 = build_graph(deps, checkpointer=saver2)
    await graph2.ainvoke(resume(True), thread_config(tid))
    restart_ms = (time.perf_counter() - t1) * 1000
    await pool2.close()

    # C. 纯读任务（不暂停）写几个 checkpoint
    saver3, pool3 = await open_checkpointer(DSN)
    graph3 = build_graph(deps, checkpointer=saver3)
    tid_q = "m-2.2-query"
    await graph3.ainvoke({"task": QUERY, "customer_name": "张伟"}, thread_config(tid_q))
    query_steps = await _count(conn, "checkpoints", tid_q)
    await pool3.close()

    for table in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
        async with conn.cursor() as cur:
            await cur.execute(f"DELETE FROM {table} WHERE thread_id LIKE 'm-2.2%'")
    await index.clear_prefix("m-2.2")
    await conn.close()

    print(f"内存 pause+resume: {mem_ms:.1f} ms")
    print(f"真库 跨重启恢复（含开池+setup）: {restart_ms:.1f} ms")
    print(f"暂停线程的行数: {counts}")
    print(f"纯问答线程的 checkpoint 步数: {query_steps}")


if __name__ == "__main__":
    asyncio.run(main())
