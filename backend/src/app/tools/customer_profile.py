"""customer_profile：按姓名查联系人 + 持有资产，返回可直接拼进提示词的一段文字。

这个工具是「只读」的第二个落地形态（第一个是注册表本身）：
- 不做模糊匹配，查不到就说查不到，不让模型拿相近的名字硬凑；
- 输出里必须同时有「到期日」与「状态」，否则模型没法判断续保优先级；
- 出库再脱敏一次，防的是未来 join 出明文，不是不信存储层。
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field

from app.store.sqlite_store import Asset, Contact, CustomerStore
from app.tools.base import Tool


class CustomerProfileArgs(BaseModel):
    # [TUTOR-JAVA] extra="forbid" ≈ Jackson 的 FAIL_ON_UNKNOWN_PROPERTIES：
    # [TUTOR-JAVA] 模型多传一个 customerName 之类的字段时直接报错，而不是静默丢掉。
    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(
        min_length=1,
        max_length=40,
        description="客户姓名，需与 CRM 登记一致；不做模糊匹配",
    )


def build_customer_profile_tool(store: CustomerStore) -> Tool:
    """把 store 闭包进 handler，注册表里只留一个 Tool 对象。"""

    async def handler(args: CustomerProfileArgs) -> str:
        # [TUTOR-JAVA] asyncio.to_thread ≈ 把这次阻塞调用丢到线程池执行，等价于
        # [TUTOR-JAVA] CompletableFuture.supplyAsync(...)，避免 sqlite 的同步 I/O 卡住事件循环。
        contact, assets = await asyncio.to_thread(_load, store, args.customer_name)
        return render(customer_name=args.customer_name, contact=contact, assets=assets)

    return Tool(
        name="customer_profile",
        description=(
            "按客户姓名查询联系人档案与持有资产，返回到期日、保单状态、最近互动日期。"
            "只读，不做模糊匹配；查不到会明确说明未找到。"
        ),
        args_schema=CustomerProfileArgs,
        handler=handler,
    )


def _load(store: CustomerStore, name: str) -> tuple[Contact | None, tuple[Asset, ...]]:
    contact = store.find_contact(name)
    if contact is None:
        return None, ()
    return contact, store.list_assets(contact.id)


def render(*, customer_name: str, contact: Contact | None, assets: tuple[Asset, ...]) -> str:
    if contact is None:
        # [TUTOR-WHY] 不列库里有谁：把名单回给模型等于把通讯录整本喂进提示词
        return f"未找到客户：{customer_name}。请确认姓名与 CRM 登记一致（本工具不做模糊匹配）。"

    lines = [
        f"客户：{contact.name}（{contact.id}）",
        f"最近互动：{contact.last_interaction_at or '无记录'}",
    ]
    if not assets:
        lines.append("持有资产：无记录")
        return "\n".join(lines)

    lines.append(f"持有资产 {len(assets)} 条：")
    for index, asset in enumerate(assets, 1):
        lines.append(
            f"{index}. {asset.product}｜状态：{asset.status}｜"
            f"到期：{asset.expires_at or '无记录'}｜年缴：{_money(asset.premium_cents)}"
        )
    return "\n".join(lines)


# [TUTOR-WHY] 金额用「分」存整数再除：浮点存钱会丢精度，Java 里用 BigDecimal 是同一件事
def _money(cents: int) -> str:
    return f"{cents / 100:,.0f} 元"
