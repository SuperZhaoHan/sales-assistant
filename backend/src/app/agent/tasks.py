"""把「图的提交动作」接到业务表：算幂等键 + 幂等写入 + 返回任务号。

为什么幂等键不放在图里、也不让模型生成：
- 图只负责流程，不该知道签名规则；
- 模型生成 key 必然不稳定（同一件事两次生成不同串），重放的语义就没了。
所以 key 固定由 ``thread_id + 工具名 + 参数`` 派生，参数按 key 排序后序列化，
保证同样输入永远算出同一个串。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.store.sqlite_store import CustomerStore


def dedupe_key(thread_id: str, tool: str, args: Mapping[str, Any]) -> str:
    """``sha256(thread_id + tool + 规范化参数)``。

    规范化 = 参数按 key 排序、紧凑分隔符、不转义中文。少这一步，
    ``{"a":1,"b":2}`` 和 ``{"b":2,"a":1}`` 会算出不同的键，重放就不幂等了。
    """

    canonical = json.dumps(
        {"thread": thread_id, "tool": tool, "args": dict(sorted(args.items()))},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_committer(store: CustomerStore) -> Callable[[Mapping[str, str]], Awaitable[str]]:
    """返回图的 ``commit_task`` 依赖：吃状态载荷，吐任务号。"""

    async def commit(payload: Mapping[str, str]) -> str:
        thread_id = str(payload["thread_id"])
        args = {
            "customer_name": str(payload.get("customer_name", "")),
            "title": str(payload["title"]),
            "due_at": str(payload.get("due_at") or ""),
        }
        task = await asyncio.to_thread(
            store.add_task,
            customer_name=args["customer_name"],
            title=args["title"],
            due_at=args["due_at"] or None,
            dedupe_key=dedupe_key(thread_id, "create_task", args),
        )
        return task.id

    return commit
