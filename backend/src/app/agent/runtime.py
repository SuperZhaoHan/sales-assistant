"""把图、持久化、业务库装成一个「可以直接被接口调用」的运行时。

为什么单独一层：``create_app`` 只该决定「启动顺序」，不该知道图依赖长什么样。
图依赖（查档/检索/提交）在真实环境里是三件不同的事，这里统一适配。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.agent.confirm import ConfirmOutcome, confirm_task
from app.agent.graph import GraphDeps, build_graph
from app.agent.persistence import open_checkpointer
from app.agent.tasks import make_committer
from app.retrieval.retriever import Retriever
from app.store.sqlite_store import CustomerStore

logger = structlog.get_logger(__name__)

CHECKPOINT_MAX_ROWS = 3


@dataclass(slots=True)
class AgentRuntime:
    """接口层只碰这三样：``confirm``、``ttl_seconds``、``saver_kind``。"""

    graph: Any
    store: CustomerStore
    ttl_seconds: int
    saver_kind: str
    _closer: Callable[[], Awaitable[None]] | None = field(default=None, repr=False)

    async def confirm(self, thread_id: str, approved: bool) -> ConfirmOutcome:
        return await confirm_task(
            self.graph,
            self.store,
            thread_id,
            approved,
            ttl_seconds=self.ttl_seconds,
        )

    async def aclose(self) -> None:
        if self._closer is not None:
            await self._closer()
            self._closer = None


def _customer_text(contact: Any) -> str:
    return (
        f"{contact.name}｜顾问 {contact.advisor}｜"
        f"最近互动 {contact.last_interaction_at or '无记录'}"
    )


async def build_agent_runtime(
    *,
    store: CustomerStore,
    retriever: Retriever,
    checkpoint_dsn: str | None,
    ttl_seconds: int,
    min_score: float = 0.0,
) -> AgentRuntime:
    """装配运行时。``checkpoint_dsn`` 为空时退回内存 saver，并在日志里明说。"""

    closer: Callable[[], Awaitable[None]] | None = None
    saver_kind = "memory"
    saver: Any

    if checkpoint_dsn:
        pg_saver, pool = await open_checkpointer(checkpoint_dsn, max_size=CHECKPOINT_MAX_ROWS)
        saver = pg_saver
        closer = pool.close
        saver_kind = "postgres"
    else:
        # [TUTOR-WHY] 内存 saver 只适合本地/CI：进程一重启，所有待确认线程就消失了。
        # 这不是「简化版」，是「另一种故障边界」，必须在启动日志里说清楚。
        from langgraph.checkpoint.memory import InMemorySaver

        saver = InMemorySaver()
        logger.warning(
            "agent_checkpointer_memory", hint="未配置 agent.checkpoint_dsn，重启会丢线程"
        )

    async def lookup_customer(name: str) -> str:
        contact = await asyncio.to_thread(store.find_contact, name)
        if contact is None:
            return f"未查到客户：{name}"
        return _customer_text(contact)

    async def retrieve_knowledge(query: str) -> str:
        hits = await asyncio.to_thread(retriever.search, query, top_k=3, min_score=min_score)
        if not hits:
            return "无相关条款"
        return "\n".join(
            f"[K{index}] {hit.chunk.title}：{hit.chunk.text}" for index, hit in enumerate(hits, 1)
        )

    deps = GraphDeps(
        lookup_customer=lookup_customer,
        retrieve_knowledge=retrieve_knowledge,
        commit_task=make_committer(store),
    )
    return AgentRuntime(
        graph=build_graph(deps, checkpointer=saver),
        store=store,
        ttl_seconds=ttl_seconds,
        saver_kind=saver_kind,
        _closer=closer,
    )
