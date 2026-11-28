"""确认动作的服务层：判断「还能不能确认」，再决定写不写。

为什么 TTL 判定要在服务层、不在图里：
- 图只负责流程，不知道「人可能走开」这件事；
- 超时是产品规则（默认拒绝），改规则不该动图；
- 判定必须发生在恢复**之前**——一旦恢复，节点就开始跑，再判就晚了。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from app.agent.graph import resume, thread_config
from app.core.errors import InternalError
from app.store.sqlite_store import CustomerStore

ConfirmStatus = Literal["confirmed", "rejected", "expired", "missing"]

HTTP_OK = 200
HTTP_CONFLICT = 409


def decision_key(thread_id: str, approved: bool) -> str:
    """确认请求的幂等键：**同一线程 + 同一决定** = 同一个请求。

    为什么把 ``approved`` 也算进去：同一个线程先点确认、再点拒绝，是两个不同的
    请求，结果必然不同；不区分就会把"拒绝"重放成"确认成功"，这是最危险的串味。
    """

    decision = "yes" if approved else "no"
    canonical = f"{thread_id}|confirm_task|{decision}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay(record: Mapping[str, str | None]) -> ConfirmOutcome:
    """把上次存下的结论原样返回。不读图、不写业务表。"""

    payload = json.loads(record.get("result") or "{}")
    status = str(payload.get("status") or record.get("status") or "")
    if status == "expired":
        return ConfirmOutcome(
            status="expired",
            http_status=HTTP_CONFLICT,
            code="CONFIRM_EXPIRED",
            age_seconds=payload.get("age_seconds"),
        )
    if status == "confirmed":
        return ConfirmOutcome(
            status="confirmed",
            http_status=HTTP_OK,
            code="CONFIRMED",
            task_id=payload.get("task_id"),
        )
    return ConfirmOutcome(status="rejected", http_status=HTTP_OK, code="REJECTED")


class GraphLike(Protocol):
    async def aget_state(self, config: Mapping[str, Any]) -> Any: ...

    async def ainvoke(self, input: Any, config: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True, slots=True)
class ConfirmOutcome:
    """一次确认动作的结果。``http_status`` 让接口层直接照着映射，不用自己再判一遍。"""

    status: ConfirmStatus
    http_status: int
    code: str
    task_id: str | None = None
    age_seconds: float | None = None


def _paused_at(created_at: str | None) -> float | None:
    """检查点的 ``created_at`` 就是「线程停下的时刻」，TTL 从它开始算。

    为什么不用 state 里的自定义字段：``interrupt`` 暂停时节点还没返回，
    写在节点里的时间戳要等恢复才有值——拿它判 TTL，永远是「刚刚暂停」。
    """

    if not created_at:
        return None
    try:
        return datetime.fromisoformat(created_at).astimezone(UTC).timestamp()
    except ValueError:
        return None


async def confirm_task(
    graph: GraphLike,
    store: CustomerStore,
    thread_id: str,
    approved: bool,
    *,
    ttl_seconds: int,
    now: float | None = None,
) -> ConfirmOutcome:
    """确认/拒绝一次待确认动作；超时则默认拒绝并留审计。

    顺序是刻意的：**先判有没有待确认 → 再判超时 → 最后才恢复**。
    任何一步放在恢复之后，都等于「先跑起来再说」，超时就失去了意义。
    """

    # ① 先查「这个请求是不是已经处理过」——重放要返回原结果，不是 409
    idem_key = decision_key(thread_id, approved)
    recorded = store.get_decision(idem_key)
    if recorded is not None:
        return _replay(recorded)

    config = thread_config(thread_id)
    snapshot = await graph.aget_state(config)

    # 没有待确认的 interrupt：要么线程不存在，要么已经处理过了
    if not snapshot.interrupts:
        return ConfirmOutcome(
            status="missing",
            http_status=HTTP_CONFLICT,
            code="NO_PENDING_CONFIRM",
        )

    requested_at = _paused_at(snapshot.created_at)
    if requested_at is None:
        # 算不出「什么时候停的」就不敢确认：宁可让人重新发起
        return ConfirmOutcome(
            status="missing",
            http_status=HTTP_CONFLICT,
            code="CONFIRM_STATE_UNKNOWN",
        )
    current = time.time() if now is None else float(now)
    age = current - requested_at

    if age > ttl_seconds:
        # 超时的处置是「拒绝」，不是「等等再说」：人可能早就走了
        # [TUTOR-WHY] 只返回 409 不给线程收尾，interrupt 会永远挂在 checkpointer 里：
        # [TUTOR-WHY] 列表页一直显示「待确认」、人反复点、对账数字虚高。
        # 所以照样 resume(False)：走「拒绝」分支到终态，interrupt 自然清空，且不会写任务。
        final = await graph.ainvoke(resume(False), config)
        if final.get("stage") == "committed":
            # 防御：超时分支绝不能写出任务；真写出来说明 resume(False) 语义被改坏了
            raise InternalError(
                "超时拒绝分支产生了任务，resume(False) 语义已损坏",
                details={"thread_id": thread_id},
            )
        # [TUTOR-WHY] 结论在动作成功之后才落库：先记结果再执行，一旦执行失败，
        # 重放就会永远拿一个"没发生过的成功"。
        store.save_decision(
            thread_id=thread_id,
            idem_key=idem_key,
            status="expired",
            detail=f"age={age:.1f}s > ttl={ttl_seconds}s",
            result=json.dumps({"status": "expired", "age_seconds": round(age, 1)}),
        )
        return ConfirmOutcome(
            status="expired",
            http_status=HTTP_CONFLICT,
            code="CONFIRM_EXPIRED",
            age_seconds=age,
        )

    final = await graph.ainvoke(resume(approved), config)
    if final.get("stage") == "committed":
        task_id = final.get("task_id")
        store.save_decision(
            thread_id=thread_id,
            idem_key=idem_key,
            status="confirmed",
            result=json.dumps({"status": "confirmed", "task_id": task_id}),
        )
        return ConfirmOutcome(
            status="confirmed",
            http_status=HTTP_OK,
            code="CONFIRMED",
            task_id=task_id,
            age_seconds=age,
        )

    store.save_decision(
        thread_id=thread_id,
        idem_key=idem_key,
        status="rejected",
        result=json.dumps({"status": "rejected"}),
    )
    return ConfirmOutcome(
        status="rejected",
        http_status=HTTP_OK,
        code="REJECTED",
        age_seconds=age,
    )
