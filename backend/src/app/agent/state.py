"""Agent 的状态形状：意图、任务阶段、停机原因与单步记录。

这里只定义「数据长什么样」和「非法值怎么办」，不放流程：
- 流程在 ``loop.py``；
- 停机判据在 ``stop.py``。

这样做的原因：状态一旦和流程写在一起，就再也测不了单个停机原因了。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.errors import ValidationError


class Intent(StrEnum):
    """这一轮用户到底想要什么。

    只有四种。新增一种必须同时改意图规则（``intent.py``）和工具映射，
    否则模型会规划出没有工具能执行的动作。
    """

    ASK_CUSTOMER = "ask_customer"
    ASK_KNOWLEDGE = "ask_knowledge"
    CREATE_TASK = "create_task"
    DELETE_TASK = "delete_task"
    CHITCHAT = "chitchat"


class TaskStage(StrEnum):
    """任务所处阶段。

    写操作只允许 ``draft -> awaiting_confirm -> committed``，
    任何一步都不能跳；``aborted`` 是唯一可以从任意阶段进入的终态。
    """

    DRAFT = "draft"
    AWAITING_CONFIRM = "awaiting_confirm"
    COMMITTED = "committed"
    ABORTED = "aborted"


class StopReason(StrEnum):
    """循环为什么停下来。五个值都是正常返回值，不是异常。"""

    DONE = "done"
    MAX_STEPS = "max_steps"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    REFUSED = "refused"


def parse_enum[E: StrEnum](enum_cls: type[E], raw: str, *, field_name: str) -> E:
    """把外部输入（请求体、配置、模型输出）转成枚举。

    非法值不能静默兜底成默认值：那会让「模型胡说」看起来像「模型正常」。
    这里统一抛 ``VALIDATION_1001``，details 里带上允许的取值，调用方可直接回显给用户。
    """

    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = [item.value for item in enum_cls]
        raise ValidationError(
            f"{field_name} 取值非法",
            details={"field": field_name, "value": raw, "allowed": allowed},
        ) from exc


@dataclass
class Step:
    """循环里的一步：想了什么、要做什么、看到了什么、花了多少 token。"""

    index: int
    thought: str = ""
    action: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    observation: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class AgentState:
    """一次任务的全部可变状态。只在 ``loop.py`` 里被写，其它模块只读。"""

    task: str
    intent: Intent = Intent.CHITCHAT
    stage: TaskStage = TaskStage.DRAFT
    steps: list[Step] = field(default_factory=list)
    answer: str | None = None
    refusal: str | None = None
    tokens_used: int = 0
    cost_usd: float = 0.0

    def record(self, step: Step) -> None:
        """记一步，并把 token / 费用累加进来。

        记账放在这里而不是每个调用点，是为了保证「step 数」和「花掉的钱」
        永远对得上——停机判据就是靠这两个数触发的。
        """

        self.steps.append(step)
        self.tokens_used += step.tokens
        self.cost_usd += step.cost_usd

    @property
    def last_observation(self) -> str | None:
        """最近一次工具返回。用来给规划器拼上下文，也用来填 ``partial``。"""

        for step in reversed(self.steps):
            if step.observation is not None:
                return step.observation
        return None


@dataclass(frozen=True)
class AgentResult:
    """循环的出口。无论成功还是半途停下，都是这个形状，调用方只需要一套逻辑。"""

    stop_reason: StopReason
    answer: str | None
    steps: list[dict[str, Any]]
    missing: list[str]
    tokens: int
    cost_usd: float

    @property
    def ok(self) -> bool:
        return self.stop_reason is StopReason.DONE

    def to_dict(self) -> dict[str, Any]:
        """接口响应用的形状。``partial`` 与 ``missing`` 在阶段 0.4 就要有。"""

        return {
            "ok": self.ok,
            "stop_reason": self.stop_reason.value,
            "answer": self.answer,
            "partial": {"step_count": len(self.steps), "steps": list(self.steps)},
            "missing": list(self.missing),
            "usage": {"tokens": self.tokens, "cost_usd": round(self.cost_usd, 6)},
        }
