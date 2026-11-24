"""停机判据：一个纯函数，五个原因。

判据顺序是刻意的，从「最不需要干活」到「必须收尾」：
1. 已经有答复 → done（哪怕同时超了预算，任务其实成功了，先认结果）；
2. 明确被拒绝 → refused（拒绝是事实，不该伪装成超步数）；
3. 步数用尽 → max_steps；
4. 超时 → timeout；
5. 预算用尽 → budget_exceeded。

预算和超时都按「已经用掉多少」判断，所以宁可多花一点点也要先把当前这步记完，
不会出现「钱花了但没记账」的情况。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.state import AgentState, StopReason
from app.core.errors import ValidationError


@dataclass(frozen=True)
class Limits:
    """一次任务的上限。三个数都必须为正，配错了在构造时就报错，不带病运行。"""

    max_steps: int = 6
    max_tokens: int = 3000
    max_cost_usd: float | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValidationError("max_steps 必须 >= 1", details={"field": "max_steps"})
        if self.max_tokens < 1:
            raise ValidationError("max_tokens 必须 >= 1", details={"field": "max_tokens"})
        if self.timeout_seconds <= 0:
            raise ValidationError("timeout_seconds 必须 > 0", details={"field": "timeout_seconds"})
        if self.max_cost_usd is not None and self.max_cost_usd <= 0:
            raise ValidationError("max_cost_usd 必须 > 0", details={"field": "max_cost_usd"})


def check_stop(
    state: AgentState,
    limits: Limits,
    *,
    now: float | None = None,
    deadline: float | None = None,
) -> StopReason | None:
    """返回该停的原因，或 ``None`` 表示还能继续。

    ``now`` / ``deadline`` 由调用方传入而不是内部读时钟，
    这样「超时」可以用 ``timeout_seconds=0`` 在 1 毫秒内测出来，不用真的 sleep 30 秒。
    """

    if state.answer is not None:
        return StopReason.DONE
    if state.refusal is not None:
        return StopReason.REFUSED
    if len(state.steps) >= limits.max_steps:
        return StopReason.MAX_STEPS
    if now is not None and deadline is not None and now >= deadline:
        return StopReason.TIMEOUT
    if state.tokens_used >= limits.max_tokens:
        return StopReason.BUDGET_EXCEEDED
    if limits.max_cost_usd is not None and state.cost_usd >= limits.max_cost_usd:
        return StopReason.BUDGET_EXCEEDED
    return None
