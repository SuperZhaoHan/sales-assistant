"""2.2 持久化：官方 Postgres checkpointer 存「状态」，薄表 agent_threads 存「索引」。

为什么是两套东西，而不是一张表全存：

- checkpointer 是 LangGraph 的**事实源**：每个 super-step 的状态快照、待写队列，
  三张官方表（``checkpoints`` / ``checkpoint_blobs`` / ``checkpoint_writes``），
  升级时跑 ``saver.setup()`` 自动迁移。自己照抄一份，等于自己维护一个会和上游漂移的 schema。
- ``agent_threads`` 是**派生索引**：只放「列表页要显示」的几个业务字段，用来按客户查
  「这条跟进线程卡在哪一步」。它不是状态，也不许把状态抄进来——
  抄进来就双写，双写总有一天两边对不上，而且没人知道该信谁。
- 索引丢了不算事故：从 checkpoints 重建即可（``rebuild_threads``）。

一句话的分工：**状态归 checkpointer，索引归 agent_threads，索引随时可重算。**
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.agent.state import TaskStage

THREAD_TABLE = "agent_threads"

# [TUTOR-WHY] 只有 id、业务键、状态、最后节点、过期时间、更新时间。
# [TUTOR-WHY] 故意没有 state 列：有它就会有人往里写，写了就分不出谁是事实源。
_THREAD_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    thread_id     text PRIMARY KEY,
    customer_name text NOT NULL DEFAULT '',
    status        text NOT NULL,
    last_node     text NOT NULL DEFAULT '',
    expires_at    timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now()
)
"""

_UPSERT = """
INSERT INTO {table} (thread_id, customer_name, status, last_node, expires_at, updated_at)
VALUES (%(thread_id)s, %(customer_name)s, %(status)s, %(last_node)s, %(expires_at)s, now())
ON CONFLICT (thread_id) DO UPDATE SET
    customer_name = EXCLUDED.customer_name,
    status        = EXCLUDED.status,
    last_node     = EXCLUDED.last_node,
    expires_at    = EXCLUDED.expires_at,
    updated_at    = now()
"""

_GET = """
SELECT thread_id, customer_name, status, last_node, expires_at, updated_at
FROM {table} WHERE thread_id = %(thread_id)s
"""

_COUNT = "SELECT count(*) FROM {table}"

_DELETE = "DELETE FROM {table} WHERE thread_id = %(thread_id)s"

_COLUMNS = """
SELECT column_name FROM information_schema.columns
WHERE table_name = %(table)s ORDER BY ordinal_position
"""

_DELETE_PREFIX = "DELETE FROM {table} WHERE thread_id LIKE %(prefix)s"

_COUNT_PREFIX = "SELECT count(*) FROM {table} WHERE thread_id LIKE %(prefix)s"

# 终态集合：进到这里就不再变，索引里的状态应当和它一致。
_FINAL_STATUSES = {TaskStage.COMMITTED.value, TaskStage.ABORTED.value}


async def open_checkpointer(
    dsn: str, *, max_size: int = 4
) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """开一个带连接池的异步 checkpointer，并保证表结构就绪。

    [TUTOR-WHY] 为什么是异步版本：FastAPI 的请求协程是 async 的，图用 ``ainvoke`` 跑；
    [TUTOR-WHY] 同步 ``PostgresSaver`` 在异步路径上会直接抛 ``NotImplementedError``
    [TUTOR-WHY] （它没实现 ``aget_tuple``）——这不是配置问题，是版本给的硬限制。

    ``setup()`` 是幂等的（内部按 migration 版本判断），每次启动跑一次安全，
    代价是一条 SELECT，不是建表。
    """

    # [TUTOR-WHY] 必须 autocommit：setup() 里有一条 ``CREATE INDEX CONCURRENTLY``，
    # [TUTOR-WHY] 它不允许跑在事务块里，默认的「隐式事务」会让启动直接报
    # [TUTOR-WHY] ``ActiveSqlTransaction``。
    pool = AsyncConnectionPool(
        dsn,
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"autocommit": True},
    )
    await pool.open()
    # [TUTOR-WHY] 3.2.1 的构造签名标注写的是「单个连接」，实现却接受连接池；
    # [TUTOR-WHY] 这里显式 ignore，是不想在类型上撒谎成「真传了一个连接」。
    saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await saver.setup()
    return saver, pool


def status_from_state(state: Mapping[str, Any]) -> str:
    """从状态里的 ``stage`` 推出索引状态；认不出来的一律当 ``draft``。

    注意：被 ``interrupt`` 挂起时，状态里 ``stage`` **仍然是 draft**（那个节点没返回），
    所以「等人确认」这个状态必须由调用方显式传，不能靠这里猜。
    """

    stage = str(state.get("stage", ""))
    if stage in _FINAL_STATUSES or stage == TaskStage.AWAITING_CONFIRM.value:
        return stage
    return TaskStage.DRAFT.value


def has_pending_interrupt(tup: CheckpointTuple) -> bool:
    """判断这个 checkpoint 是不是「卡在 interrupt 上等人」。

    依据不是猜状态，而是 ``pending_writes`` 里真的挂着 ``__interrupt__`` 这一格——
    冻结的图会把待写项留在这里，恢复时从这里接着走。
    """

    return any(channel == "__interrupt__" for _, channel, _ in (tup.pending_writes or ()))


def last_node_from_state(state: Mapping[str, Any]) -> str:
    """节点轨迹的最后一格。没有轨迹（比如刚建线程）就返回空串，不返回 ``None``。"""

    trace = state.get("trace") or []
    return str(trace[-1]) if trace else ""


@dataclass
class ThreadIndex:
    """``agent_threads`` 的读写入口。只接受业务字段，**不接受 state**。

    这就是「禁止把 state 复制进业务表」在代码里的落点：想抄也没有入口。
    """

    conn: AsyncConnection

    async def ensure_schema(self) -> None:
        async with self.conn.cursor() as cur:
            # [TUTOR-WHY] 表名是模块常量、不来自请求，bandit 对「常量 .format」不报 B608；
            # [TUTOR-WHY] 别照着别处习惯先加 nosec——多余的那条会反过来告警。
            await cur.execute(_THREAD_DDL.format(table=THREAD_TABLE))
        await self.conn.commit()

    async def remember(
        self,
        thread_id: str,
        *,
        status: str,
        last_node: str,
        customer_name: str = "",
        ttl_seconds: int | None = None,
    ) -> None:
        """幂等 upsert：同一个 thread_id 写十次也只有一行。

        ``ttl_seconds`` 只管「列表页多久后不再显示」，不影响 checkpoint——
        状态不会因为索引过期就消失。
        """

        expires_at: datetime | None = None
        if ttl_seconds is not None:
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        async with self.conn.cursor() as cur:
            await cur.execute(
                _UPSERT.format(table=THREAD_TABLE),
                {
                    "thread_id": thread_id,
                    "customer_name": customer_name,
                    "status": status,
                    "last_node": last_node,
                    "expires_at": expires_at,
                },
            )
        await self.conn.commit()

    async def get(self, thread_id: str) -> dict[str, Any] | None:
        async with self.conn.cursor() as cur:
            await cur.execute(_GET.format(table=THREAD_TABLE), {"thread_id": thread_id})
            row = await cur.fetchone()
            if row is None:
                return None
            columns = [desc.name for desc in (cur.description or [])]
        return dict(zip(columns, row, strict=True))

    async def count(self) -> int:
        async with self.conn.cursor() as cur:
            await cur.execute(_COUNT.format(table=THREAD_TABLE))
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def columns(self) -> list[str]:
        """表结构快照，用来做「不许有 state 列」这种结构级断言。"""

        async with self.conn.cursor() as cur:
            await cur.execute(_COLUMNS, {"table": THREAD_TABLE})
            rows = await cur.fetchall()
        return [str(row[0]) for row in rows]

    async def clear_prefix(self, prefix: str) -> None:
        """按前缀清理，测试与手动回收用；不碰 checkpoint。"""

        async with self.conn.cursor() as cur:
            await cur.execute(_DELETE_PREFIX.format(table=THREAD_TABLE), {"prefix": f"{prefix}%"})
        await self.conn.commit()

    async def count_prefix(self, prefix: str) -> int:
        async with self.conn.cursor() as cur:
            await cur.execute(_COUNT_PREFIX.format(table=THREAD_TABLE), {"prefix": f"{prefix}%"})
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def forget(self, thread_id: str) -> None:
        """测试与手动清理用：只删索引行，**不碰 checkpoint**。"""

        async with self.conn.cursor() as cur:
            await cur.execute(_DELETE.format(table=THREAD_TABLE), {"thread_id": thread_id})
        await self.conn.commit()


async def rebuild_threads(saver: BaseCheckpointSaver, index: ThreadIndex) -> int:
    """从 checkpoints 重建索引，返回重建条数。

    每条线程取 step 最大的那个 checkpoint，从 ``channel_values`` 里读
    客户名 / 阶段 / 节点轨迹——这些本来就是图的状态，索引只是它的投影。
    """

    latest: dict[str, tuple[int, Any]] = {}
    async for tup in saver.alist(None):
        thread_id = str(tup.config["configurable"]["thread_id"])
        step = int((tup.metadata or {}).get("step", 0))
        if thread_id not in latest or step >= latest[thread_id][0]:
            latest[thread_id] = (step, tup)

    for thread_id, (_, tup) in latest.items():
        values = tup.checkpoint.get("channel_values") or {}
        # 「暂停等人」这件事不在状态里（节点没返回），只能从挂起的待写项读出来。
        if has_pending_interrupt(tup):
            status, last_node = TaskStage.AWAITING_CONFIRM.value, "await_confirm"
        else:
            status = status_from_state(values)
            last_node = last_node_from_state(values)
        await index.remember(
            thread_id,
            customer_name=str(values.get("customer_name", "")),
            status=status,
            last_node=last_node,
        )
    return len(latest)
