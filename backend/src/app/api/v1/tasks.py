"""写操作确认接口：``POST /v1/tasks/confirm``。

接口只做三件事：收参数、交给服务层判、把结果映射成 HTTP。
TTL/幂等/审计都在服务层，接口改动不该影响这些规则。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent.runtime import AgentRuntime
from app.api.deps import get_agent_runtime
from app.core.errors import ConflictError

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
    runtime: AgentRuntime = Depends(get_agent_runtime),
) -> ConfirmResponse:
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
