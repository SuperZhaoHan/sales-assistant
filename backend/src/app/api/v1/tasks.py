"""写操作确认接口：``POST /v1/tasks/confirm``。

接口只做三件事：收参数、交给服务层判、把结果映射成 HTTP。
TTL/幂等/审计都在服务层，接口改动不该影响这些规则。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent.runtime import AgentRuntime
from app.api.deps import get_agent_runtime, get_store
from app.api.v1.actor import current_actor
from app.core.errors import ConflictError, PermissionDeniedError
from app.security.roles import Actor, ensure_confirm_permission
from app.store.sqlite_store import CustomerStore

router = APIRouter(prefix="/tasks", tags=["tasks"])


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=64, description="会话 id，由发起方生成并持有")
    approved: bool = Field(description="True=确认执行，False=拒绝")


class ConfirmResponse(BaseModel):
    status: str
    task_id: str | None = None


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_task_endpoint(
    payload: ConfirmRequest,
    actor: Actor = Depends(current_actor),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    store: CustomerStore = Depends(get_store),
) -> ConfirmResponse:
    # [TUTOR-WHY] 权限判定写在接口层、不在 confirm 服务层：服务层只管「能不能确认」，
    # 不认身份；同一份服务被脚本/定时任务调用时不该被 HTTP 的角色绑住。
    try:
        ensure_confirm_permission(actor)
    except PermissionDeniedError:
        # [TUTOR-WHY] 拒绝也要留痕：谁、什么角色、想确认哪个线程。
        # 只记"被拒了"不记"谁被拒"，出了事查不出来。
        store.record_action(
            thread_id=payload.thread_id,
            kind="confirm_task",
            status="forbidden",
            detail=f"actor={actor.actor_id} role={actor.role}",
        )
        raise

    outcome = await runtime.confirm(payload.thread_id, payload.approved)
    if outcome.http_status != 200:
        # [TUTOR-WHY] 409 而不是 400：请求本身合法，是「现在的状态不允许」——
        # 语义上属于冲突，客户端据此判断是该刷新还是该放弃。
        # 用项目自己的 ConflictError：全局处理器会保留 code/status，而不是丢成一句 409。
        raise ConflictError(
            f"确认动作被拒绝：{outcome.code}",
            details={"code": outcome.code, "status": outcome.status},
        )
    return ConfirmResponse(status=outcome.status, task_id=outcome.task_id)
