"""2.7 计时：撤销一条待办要多久，以及「行还在」的证据。"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.confirm import confirm_task
from app.agent.graph import GraphDeps, build_graph, thread_config
from app.store.sqlite_store import CustomerStore

TTL = 300


async def main() -> None:
    store = CustomerStore(":memory:").open()
    task = store.add_task(customer_name="张伟", title="3 天后回访", dedupe_key="m-del-1")

    async def lookup_customer(name: str) -> str:
        return f"{name}，保单 2026-12-05 到期"

    async def retrieve_knowledge(_q: str) -> str:
        return "犹豫期 15 天内退保按现金价值计算"

    async def commit_task(_payload: dict[str, str]) -> str:
        raise AssertionError("删除路径不该建任务")

    async def find_task(customer_name: str) -> str | None:
        found = store.find_open_task(customer_name)
        return found.id if found is not None else None

    async def delete_task(task_id: str) -> None:
        store.close_task(task_id)

    deps = GraphDeps(
        lookup_customer=lookup_customer,
        retrieve_knowledge=retrieve_knowledge,
        commit_task=commit_task,
        find_task=find_task,
        delete_task=delete_task,
    )
    graph = build_graph(deps, checkpointer=InMemorySaver())
    thread = thread_config("m-del")

    started = time.perf_counter()
    paused = await graph.ainvoke({"task": "把张伟那条待办删了", "customer_name": "张伟"}, thread)
    pause_ms = (time.perf_counter() - started) * 1000
    snapshot = await graph.aget_state(thread)
    requested = datetime.fromisoformat(str(snapshot.created_at)).timestamp()

    started = time.perf_counter()
    outcome = await confirm_task(graph, store, "m-del", True, ttl_seconds=TTL, now=requested + 1)
    confirm_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    replay = await confirm_task(graph, store, "m-del", True, ttl_seconds=TTL, now=requested + 2)
    replay_ms = (time.perf_counter() - started) * 1000

    kept = store.get_task(task.id)
    print(f"暂停（等人点确认）   {pause_ms:6.1f} ms   __interrupt__={'__interrupt__' in paused}")
    print(f"确认后执行           {confirm_ms:6.1f} ms   status={outcome.status}")
    print(f"  删掉的是 {outcome.task_id}")
    print(f"重放一次             {replay_ms:6.1f} ms   status={replay.status}")
    print(
        f"行还在={kept is not None}  status={kept.status if kept else '-'}  "
        f"deleted_at={kept.deleted_at if kept else '-'}"
    )
    print(
        f"未删条数={store.count_tasks()}  含已删={store.count_tasks(include_deleted=True)}  "
        f"审计={len(store.list_actions('m-del'))}"
    )
    store.close()


if __name__ == "__main__":
    asyncio.run(main())
