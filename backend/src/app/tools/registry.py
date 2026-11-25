"""工具注册表：工具名的唯一出处。

阶段 0 里，``customer_profile`` 只是 ``planner.py`` 里的一个字符串，
打错一个字母不会有任何报错——假执行器会返回"无数据"兜住。
注册表的作用就是把这种"静默失败"变成"当场报错"。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.errors import ValidationError
from app.tools.base import Tool

# [TUTOR-JAVA] 模块级字典 ≈ 全局 ConcurrentHashMap<String, Tool>，import 期单线程写，无需加锁。
_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    """登记一个工具；同名重复登记视为错误（与提示词模板同一套思路）。"""
    # [TUTOR-WHY] 先查后写：运行期动态热注册时要加锁，现在只在 import 期调用，单线程安全。
    if tool.name in _TOOLS:
        raise ValidationError(
            f"工具已存在：{tool.name}",
            details={"tool": tool.name, "available": sorted(_TOOLS)},
        )
    _TOOLS[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool:
    tool = _TOOLS.get(name)
    if tool is None:
        raise ValidationError(
            f"未知的工具：{name}",
            details={"tool": name, "available": sorted(_TOOLS)},
        )
    return tool


def list_tools() -> tuple[Tool, ...]:
    """按登记顺序返回，顺序就是给模型看的工具清单顺序。"""
    return tuple(_TOOLS.values())


def requires_confirmation(name: str) -> bool:
    return get_tool(name).requires_confirmation


def non_idempotent_writes() -> tuple[str, ...]:
    """列出"不能幂等、但已写明理由"的写工具——审计和告警要盯的就是这几条。"""
    return tuple(tool.name for tool in _TOOLS.values() if tool.is_write and not tool.idempotent)


async def execute(name: str, args: Mapping[str, Any]) -> str:
    """按名字执行工具。未知名字与非法参数都在这里被拦住。"""
    # [TUTOR-JAVA] ≈ DispatcherServlet 的分发：拿字符串查表 → 得到实例 → 调它的 run()。
    return await get_tool(name).run(args)


def reset_registry() -> None:
    """测试用：清空注册表，模拟进程重启。"""
    # [TUTOR-WHY] 测试专用：用例之间互相污染时，光靠 fixture 顺序很难查。
    _TOOLS.clear()
