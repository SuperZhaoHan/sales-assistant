"""启动期装配：开存储、灌种子、把工具登记进注册表。

放在项目自己的模块里，不直接写进 ``lifespan``：以后加工具只要改这里一行，
``main.py`` 保持「只负责编排启动顺序」。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.store.sqlite_store import CustomerStore
from app.tools.customer_profile import build_customer_profile_tool
from app.tools.registry import list_tools, register

# [TUTOR-JAVA] 这张表 ≈ 一份 List<Function<Store, Tool>> 工厂清单，顺序即注册顺序
_DOMAIN_TOOLS = (build_customer_profile_tool,)


def open_store(store_path: str | Path, seed_dir: str | Path) -> CustomerStore:
    """开库 + 建表 + 灌种子；``store_path`` 是 ``:memory:`` 时跳过建目录。"""
    if str(store_path) != ":memory:":
        Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    store = CustomerStore(store_path).open()
    store.load_seed(seed_dir)
    return store


def register_domain_tools(
    store: CustomerStore, builders: Sequence = _DOMAIN_TOOLS
) -> tuple[str, ...]:
    """把业务工具登记进注册表。

    已经登记过就跳过：测试里会反复创建 app，启动期重复登记不该被当成错误。
    """
    existing = {tool.name for tool in list_tools()}
    for builder in builders:
        tool = builder(store)
        # [TUTOR-WHY] 启动期重复登记跳过：测试里会反复建 app，不该在第二十次炸
        if tool.name not in existing:
            register(tool)
            existing.add(tool.name)
    return tuple(tool.name for tool in list_tools())
