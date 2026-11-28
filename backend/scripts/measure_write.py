"""2.3 计时：确认前 0 行 → 确认后 1 行；重放 3 次仍 1 行。

跑法：.venv/bin/python scripts/measure_write.py
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import GraphDeps, build_graph, resume, thread_config
from app.agent.tasks import make_committer
from app.store.sqlite_store import CustomerStore

TASK = "帮张伟建个待办，3 天后提醒我打电话确认续保"


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = CustomerStore(Path(tmp) / "app.db").open()

        async def lookup_customer(_name: str) -> str:
            return "张伟，保单 2026-12-05 到期"

        async def retrieve_knowledge(_query: str) -> str:
            return "犹豫期 15 天内退保按现金价值计算"

        deps = GraphDeps(
            lookup_customer=lookup_customer,
            retrieve_knowledge=retrieve_knowledge,
            commit_task=make_committer(store),
        )
        graph = build_graph(deps, checkpointer=InMemorySaver())
        config = thread_config("t-23-measure")

        t0 = time.perf_counter()
        await graph.ainvoke({"task": TASK, "customer_name": "张伟"}, config)
        pause_ms = (time.perf_counter() - t0) * 1000
        rows_after_pause = store.count_tasks()

        t1 = time.perf_counter()
        final = await graph.ainvoke(resume(True), config)
        confirm_ms = (time.perf_counter() - t1) * 1000
        rows_after_confirm = store.count_tasks()

        commit = make_committer(store)
        payload = {"thread_id": "t-23-measure", "customer_name": "张伟", "title": "为张伟确认续保"}
        t2 = time.perf_counter()
        for _ in range(3):
            await commit(payload)
        replay_ms = (time.perf_counter() - t2) * 1000
        rows_after_replay = store.count_tasks()

        print(f"暂停（走到 interrupt）        {pause_ms:6.1f} ms   tasks={rows_after_pause}")
        print(f"确认一次（resume=True）       {confirm_ms:6.1f} ms   tasks={rows_after_confirm}")
        print(f"重放 3 次                     {replay_ms:6.1f} ms   tasks={rows_after_replay}")
        print(f"任务号                        {final['task_id']}")
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
