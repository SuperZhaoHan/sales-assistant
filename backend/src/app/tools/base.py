"""工具基类：一个工具 = 名字 + 说明 + 参数 schema + 是否写操作 + 能否幂等。

为什么把「是否写操作」放在类型上，而不是注释里：
- 只读工具可以随便跑，跑错了最多是读到旧数据；
- 写工具跑错会留下外部副作用（建错任务、多扣一次款）。
这两类工具的安全要求完全不同，所以必须在登记时就分开，而不是等执行时再判断。

``idempotent`` 与 ``non_idempotent_reason`` 是一对：
写工具要么能安全重放（幂等），要么明确写清楚"为什么不能"。
两者都没有的写工具会被注册表拒掉——这是 1.1 的验收线。
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError

# 工具名是规划器、日志、审计三处共用的键，必须能一眼看出是谁
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

Handler = Callable[[Any], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class Tool:
    """一个可被 Agent 调用的工具。

    ``args_schema`` 是 pydantic 模型：它同时承担"文档"和"校验"两件事——
    模型看到的参数说明与代码里的校验规则来自同一个定义，不会不一致。
    """

    name: str
    description: str
    args_schema: type[BaseModel]
    handler: Handler
    is_write: bool = False
    idempotent: bool = False
    non_idempotent_reason: str = field(default="")

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValidationError(
                f"工具名必须是蛇形小写（小写字母开头，可含数字与下划线）：{self.name}",
                details={"tool": self.name},
            )
        if not self.description.strip():
            raise ValidationError(
                f"工具 {self.name} 缺少 description（模型只能靠它决定要不要调）",
                details={"tool": self.name},
            )
        if not inspect.iscoroutinefunction(self.handler):
            raise ValidationError(
                f"工具 {self.name} 的 handler 必须是 async 函数："
                "同步实现会阻塞事件循环，把并发请求一起拖慢",
                details={"tool": self.name},
            )
        if not self.is_write and self.non_idempotent_reason:
            raise ValidationError(
                f"工具 {self.name} 是只读工具，不该声明 non_idempotent_reason",
                details={"tool": self.name},
            )
        if self.is_write and not self.idempotent and not self.non_idempotent_reason.strip():
            raise ValidationError(
                f"写工具 {self.name} 必须声明 idempotent=True，或写明 non_idempotent_reason",
                details={"tool": self.name, "is_write": True, "idempotent": False},
            )

    @property
    def requires_confirmation(self) -> bool:
        """写操作一律要确认；只读操作不需要。阶段 2 的确认流就挂在这个判断上。"""
        return self.is_write

    def validate_args(self, args: Mapping[str, Any]) -> BaseModel:
        """参数校验失败要带上工具名：多工具并存时，只说"字段错了"根本定位不到。"""
        try:
            return self.args_schema.model_validate(dict(args))
        except PydanticValidationError as exc:
            raise ValidationError(
                f"工具 {self.name} 的参数不合法：{exc.error_count()} 处错误",
                details={
                    "tool": self.name,
                    "errors": [
                        {"loc": list(err["loc"]), "msg": err["msg"]} for err in exc.errors()
                    ],
                },
            ) from exc

    async def run(self, args: Mapping[str, Any]) -> str:
        """校验参数 → 调 handler → 保证返回字符串（下游要拼进 observation）。"""
        validated = self.validate_args(args)
        result = await self.handler(validated)
        if not isinstance(result, str):
            raise ValidationError(
                f"工具 {self.name} 的返回值必须是字符串，实际是 {type(result).__name__}",
                details={"tool": self.name},
            )
        return result
