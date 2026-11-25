"""工具注册表的验收：只读直接执行，写工具必须能幂等或写明为什么不能。

统一约定：
- 只读工具（is_write=False）不需要确认，直接执行；
- 写工具（is_write=True）必须在登记时就交代清楚——要么 idempotent=True，
  要么给出一句 non_idempotent_reason。两个都没有的，注册阶段就拒掉。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.errors import ValidationError
from app.tools import (
    Tool,
    execute,
    get_tool,
    list_tools,
    non_idempotent_writes,
    register,
    requires_confirmation,
    reset_registry,
)


class ProfileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_name: str
    content: str


async def _read_profile(args: ProfileArgs) -> str:
    return f"客户 {args.customer_name}：保单 2026-03-01 到期"


async def _create_task(args: TaskArgs) -> str:
    return f"已为 {args.customer_name} 建任务：{args.content}"


def _read_tool() -> Tool:
    return Tool(
        name="customer_profile",
        description="按客户名查资料与持有资产",
        args_schema=ProfileArgs,
        handler=_read_profile,
    )


def _write_tool(name: str = "create_task", **kwargs: Any) -> Tool:
    return Tool(
        name=name,
        description="创建跟进任务",
        args_schema=TaskArgs,
        handler=_create_task,
        is_write=True,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    reset_registry()


async def test_read_tool_runs_without_confirmation() -> None:
    register(_read_tool())

    assert requires_confirmation("customer_profile") is False
    text = await execute("customer_profile", {"customer_name": "张总"})
    assert text == "客户 张总：保单 2026-03-01 到期"


def test_write_tool_with_idempotency_is_accepted() -> None:
    tool = register(_write_tool(idempotent=True))

    assert tool.is_write is True
    assert requires_confirmation("create_task") is True
    assert non_idempotent_writes() == ()


def test_write_tool_without_idempotency_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        register(_write_tool())

    error = exc_info.value
    assert error.details["tool"] == "create_task"
    assert "idempotent" in str(error)


def test_write_tool_can_be_exempted_with_a_reason() -> None:
    tool = register(
        _write_tool(
            name="settle_order",
            non_idempotent_reason="外部结算接口不支持幂等键，重放会多扣一次",
        )
    )

    assert tool.idempotent is False
    assert tool.non_idempotent_reason
    assert non_idempotent_writes() == ("settle_order",)


def test_duplicate_tool_name_is_rejected() -> None:
    register(_read_tool())

    with pytest.raises(ValidationError) as exc_info:
        register(_read_tool())

    assert exc_info.value.details["tool"] == "customer_profile"


def test_unknown_tool_lists_available_names() -> None:
    register(_read_tool())

    with pytest.raises(ValidationError) as exc_info:
        # 拼错一个字母，必须当场报，而不是等到执行时才发现没有实现
        get_tool("customer_profil")

    assert exc_info.value.details["available"] == ["customer_profile"]


async def test_bad_args_are_rejected_with_tool_name() -> None:
    register(_read_tool())

    with pytest.raises(ValidationError) as exc_info:
        await execute("customer_profile", {"customerName": "张总"})

    assert exc_info.value.details["tool"] == "customer_profile"


def test_tool_name_must_be_snake_case() -> None:
    with pytest.raises(ValidationError) as exc_info:
        register(
            Tool(
                name="CustomerProfile",
                description="查客户",
                args_schema=ProfileArgs,
                handler=_read_profile,
            )
        )

    assert exc_info.value.details["tool"] == "CustomerProfile"


def test_sync_handler_is_rejected() -> None:
    def sync_handler(args: ProfileArgs) -> str:  # noqa: ARG001
        return "同步实现"

    with pytest.raises(ValidationError) as exc_info:
        register(
            Tool(
                name="sync_tool",
                description="同步工具",
                args_schema=ProfileArgs,
                handler=sync_handler,  # type: ignore[arg-type]
            )
        )

    assert "async" in str(exc_info.value)


def test_read_tool_with_reason_is_rejected() -> None:
    # 只读工具不需要交代"为什么不能幂等"，写了说明字段用错了
    with pytest.raises(ValidationError) as exc_info:
        register(
            Tool(
                name="read_only_tool",
                description="只读",
                args_schema=ProfileArgs,
                handler=_read_profile,
                non_idempotent_reason="多余",
            )
        )

    assert exc_info.value.details["tool"] == "read_only_tool"


def test_list_tools_is_insertion_ordered() -> None:
    register(_read_tool())
    register(_write_tool(idempotent=True))

    assert [tool.name for tool in list_tools()] == ["customer_profile", "create_task"]
