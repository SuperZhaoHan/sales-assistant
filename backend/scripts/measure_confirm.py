"""2.4 计时：TTL 内确认 vs 超时拒绝；后者必须 0 行 + 一条 expired 审计。

跑法：.venv/bin/python scripts/measure_confirm.py
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.confirm import confirm_task
from app.agent.graph import GraphDeps, build_graph, thread_config
from app.agent.tasks import make_committer
from app.store.sqlite_store import CustomerStore

TASK = "帮张伟建个待办，3 天后提醒我打电话确认续保"
TTL = 300


async def pause(graph, thread_id: str) -> float:
    await graph.ainvoke({"task": TASK, "customer_name": "张伟"}, thread_config(thread_id))
    snapshot = await graph.aget_state(thread_config(thread_id))
    return datetime.fromisoformat(str(snapshot.created_at)).timestamp()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = CustomerStore(Path(tmp) / "app.db").open()

        async def lookup_customer(name: str) -> str:
            return f"{name}，保单 2026-12-05 到期"

        async def retrieve_knowledge(_query: str) -> str:
            return "犹豫期 15 天内退保按现金价值计算"

        deps = GraphDeps(
            lookup_customer=lookup_customer,
            retrieve_knowledge=retrieve_knowledge,
            commit_task=make_committer(store),
        )
        graph = build_graph(deps, checkpointer=InMemorySaver())

        requested = await pause(graph, "t-24-fast")
        started = time.perf_counter()
        ok = await confirm_task(graph, store, "t-24-fast", True, ttl_seconds=TTL, now=requested + 1)
        confirm_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        replays = [
            await confirm_task(graph, store, "t-24-fast", True, ttl_seconds=TTL, now=requested + 2)
            for _ in range(3)
        ]
        replay_ms = (time.perf_counter() - started) * 1000

        await pause(graph, "t-24-slow")
        started = time.perf_counter()
        late = await confirm_task(
            graph, store, "t-24-slow", True, ttl_seconds=TTL, now=requested + TTL + 1
        )
        expired_ms = (time.perf_counter() - started) * 1000

        print(f"TTL 内确认          {confirm_ms:6.1f} ms  status={ok.status}")
        print(f"  tasks={store.count_tasks()}")
        print(f"超时确认（默认拒绝）  {expired_ms:6.1f} ms  status={late.status} code={late.code}")
        same = len({item.task_id for item in replays}) == 1
        print(f"重放 3 次           {replay_ms:6.1f} ms  status={replays[0].status}")
        print(f"  同一个任务号={same}  tasks={store.count_tasks()}  审计={store.count_actions()}")
        print(f"任务行数            {store.count_tasks()}   审计行数 {store.count_actions()}")
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
