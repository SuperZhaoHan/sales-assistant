"""把客户档案压成「模板变量」：三段文本 + 一份事实清单。

为什么单独一层：
- ``customer_profile`` 是给模型看的**一段话**，这里要的是**三个变量**，形状不一样；
- 事实清单（``facts``）是给「事后校验」用的：模型写完摘要，要能检查它引用过哪条事实；
- 这一层不调模型、不碰网络，纯读库 + 拼字符串，所以能单独测。

查不到客户时**不返回半成品**：``found=False``，三段变量全空，由调用方决定怎么说不调模型。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.store.sqlite_store import Asset, Contact, CustomerStore

NOT_FOUND_TEXT = "未找到客户：{name}。请确认姓名与 CRM 登记一致（不做模糊匹配）。"


@dataclass(frozen=True, slots=True)
class Brief:
    """一次查档的结果。``found=False`` 时三个变量都是空串，避免把半成品喂给模板。"""

    customer_name: str
    found: bool
    profile: str
    assets: str
    history: str
    facts: tuple[str, ...]

    def variables(self) -> dict[str, str]:
        """模板要的三个占位符：正好对上 templates.yaml 里的 {profile}/{assets}/{history}。"""
        return {"profile": self.profile, "assets": self.assets, "history": self.history}


def build_brief(store: CustomerStore, customer_name: str) -> Brief:
    """按姓名查档。查不到就返回 ``found=False``，不抛异常、不猜近似的人。"""
    contact = store.find_contact(customer_name)
    if contact is None:
        return Brief(
            customer_name=customer_name, found=False, profile="", assets="", history="", facts=()
        )
    assets = store.list_assets(contact.id)
    return Brief(
        customer_name=contact.name,
        found=True,
        profile=_profile(contact),
        assets=_assets(assets),
        history=_history(contact),
        facts=_facts(contact, assets),
    )


def _profile(contact: Contact) -> str:
    # [TUTOR-WHY] 顾问也要带上：换人跟进时销售得知道跟谁对账
    return f"客户：{contact.name}（{contact.id}）；负责顾问：{contact.advisor}"


def _history(contact: Contact) -> str:
    # [TUTOR-WHY] 空日期说「无记录」，不留空白：模型爱把空白补成一句话
    return f"最近互动：{contact.last_interaction_at or '无记录'}"


def _assets(assets: tuple[Asset, ...]) -> str:
    # [TUTOR-WHY] 没有资产是有此人但没保单，和「查无此人」必须分开说
    if not assets:
        return "持有资产：无记录"
    lines = [f"持有资产 {len(assets)} 条："]
    for index, asset in enumerate(assets, start=1):
        lines.append(
            f"{index}. {asset.product}｜状态：{asset.status}｜"
            f"到期：{asset.expires_at or '无记录'}｜年缴：{_yuan(asset.premium_cents)}"
        )
    return "\n".join(lines)


def _facts(contact: Contact, assets: tuple[Asset, ...]) -> tuple[str, ...]:
    """把「可以用来校验的原文」挑出来：到期日、状态、最近互动。去重、按出现顺序。"""
    candidates = [asset.expires_at for asset in assets]
    candidates += [asset.status for asset in assets]
    candidates.append(contact.last_interaction_at)
    seen: list[str] = []
    for value in candidates:
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def _yuan(cents: int) -> str:
    # [TUTOR-WHY] 金额用「分」存整数再除：浮点存钱会丢精度，Java 里用 BigDecimal 同理
    return f"{cents / 100:,.0f} 元"


def not_found_text(customer_name: str) -> str:
    """查无此人时的固定话术。调用方用它直接回话，不调模型，token 花 0。"""
    return NOT_FOUND_TEXT.format(name=customer_name)
