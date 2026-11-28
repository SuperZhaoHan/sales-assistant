"""create_task：唯一一个真正写业务表的工具。

设计上的三条硬约束：

1. **写工具**：``is_write=True``，注册表会拦下没声明幂等/没写理由的写工具；
2. **必须人工确认**：``requires_confirmation`` 由 ``is_write`` 推出，图里的
   ``await_confirm`` 就是它的落点；
3. **幂等键由调用方给**，不接受模型自编：模型很容易把 key 编成随机串，
   那样重放就变成两条任务。key 只能用 ``thread_id + 工具名 + 参数`` 算出来。

所以参数里保留 ``dedupe_key`` 字段，但描述里明确写「由运行时算好传入」。
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.store.sqlite_store import CustomerStore
from app.tools.base import Tool


class CreateTaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(
        min_length=1, max_length=40, description="客户姓名，需与 CRM 登记一致"
    )
    title: str = Field(min_length=1, max_length=80, description="任务标题，一句话说清要做什么")
    due_at: str | None = Field(default=None, max_length=40, description="截止时间，ISO 日期或留空")
    dedupe_key: str = Field(
        min_length=8,
        max_length=64,
        description="幂等键，由运行时按 thread_id + 工具名 + 参数算出后传入，模型不得自编",
    )


def build_create_task_tool(store: CustomerStore) -> Tool:
    async def handler(args: CreateTaskArgs) -> str:
        # [TUTOR-JAVA] asyncio.to_thread ≈ 把阻塞的 sqlite 写丢到线程池，等价于 supplyAsync。
        task = await asyncio.to_thread(
            store.add_task,
            customer_name=args.customer_name,
            title=args.title,
            dedupe_key=args.dedupe_key,
            due_at=args.due_at,
        )
        return f"任务已就绪：{task.id}｜{task.title}｜状态 {task.status}"

    return Tool(
        name="create_task",
        description=(
            "为客户创建一条跟进任务（写操作，需用户确认）。"
            "同一幂等键重复调用只落一行，返回同一个任务号。"
        ),
        args_schema=CreateTaskArgs,
        handler=handler,
        is_write=True,
        # [TUTOR-WHY] 幂等的根在存储层（派生 id + UNIQUE + INSERT OR IGNORE），工具层只是入口。
        idempotent=True,
    )
