"""delete_task：第二个写工具，用来证明「写操作由工具属性触发确认」，而不是靠词表。

三条和 create_task 一样的硬约束：``is_write=True``、``idempotent=True``、幂等键由运行时传入。

**删除是逻辑删除**：只把 ``status`` 改成 ``deleted`` 并记 ``deleted_at``，不删行。
理由写在存储层 ``close_task`` 的 docstring 里：审计要查「谁什么时候撤的」、
报表要算「建了多少撤了多少」、误删要能恢复——物理删除三件事都做不了。
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ValidationError
from app.store.sqlite_store import CustomerStore
from app.tools.base import Tool


class DeleteTaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(
        min_length=1, max_length=64, description="要撤销的任务号，必须是当前会话里已存在的任务"
    )
    dedupe_key: str = Field(
        min_length=8,
        max_length=64,
        description="幂等键，由运行时按 thread_id + 工具名 + 参数算出后传入，模型不得自编",
    )


def build_delete_task_tool(store: CustomerStore) -> Tool:
    async def handler(args: DeleteTaskArgs) -> str:
        task = await asyncio.to_thread(store.close_task, args.task_id)
        if task is not None:
            return f"任务已撤销：{task.id}｜{task.title}｜status={task.status}"
        # 走到这里说明 UPDATE 影响 0 行：要么没这条，要么已经删过
        existing = await asyncio.to_thread(store.get_task, args.task_id)
        if existing is None:
            raise ValidationError("任务不存在", details={"task_id": args.task_id})
        return f"任务已是撤销状态，未做改动：{existing.id}"

    return Tool(
        name="delete_task",
        description=(
            "撤销一条跟进任务（写操作，需用户确认）。逻辑删除：保留行与撤销时间，可追溯、可恢复。"
            "同一幂等键重复调用不会删两次。"
        ),
        args_schema=DeleteTaskArgs,
        handler=handler,
        is_write=True,
        idempotent=True,
    )
