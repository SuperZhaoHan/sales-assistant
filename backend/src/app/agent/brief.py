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

# 一次摘要里最多展示几条资产。理由不是「模型记不住」，而是 token 与注意力：
# 几十条保单铺进去，真正该续的那条会被淹掉。超出部分只给条数，不给明细。
MAX_ASSETS_IN_PROMPT = 5

# 保险域里的失效状态取值；失效件排到后面，别占掉前 5 条额度。
_EXPIRED_STATUS = "已失效"


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


def build_brief(
    store: CustomerStore,
    customer_name: str,
    *,
    max_assets: int = MAX_ASSETS_IN_PROMPT,
) -> Brief:
    """按姓名查档。查不到就返回 ``found=False``，不抛异常、不猜近似的人。

    ``max_assets`` 控制进提示词的资产条数；被裁掉的资产既不出现在 ``assets``，
    也不进 ``facts`` —— 模型没见过的数据，不许进可校验清单。
    """
    contact = store.find_contact(customer_name)
    if contact is None:
        return Brief(
            customer_name=customer_name, found=False, profile="", assets="", history="", facts=()
        )
    # [TUTOR-WHY] 每次调用都现查库：不缓存、不记忆。库改了，下一次就是新答案
    assets = store.list_assets(contact.id)
    shown, omitted = split_assets(assets, max_assets)
    return Brief(
        customer_name=contact.name,
        found=True,
        profile=_profile(contact),
        assets=_assets(shown, omitted),
        history=_history(contact),
        facts=_facts(contact, shown),
    )


def split_assets(
    assets: tuple[Asset, ...], max_assets: int = MAX_ASSETS_IN_PROMPT
) -> tuple[tuple[Asset, ...], int]:
    """排序后截断，返回（要展示的资产，被裁掉的条数）。

    排序依据：先看还有效的，再看到期日近的。原因很实际 —— 已失效的保单
    还占着前几条额度，销售一眼看到的全是废单。
    """
    ordered = sorted(assets, key=_priority)
    shown = tuple(ordered[:max_assets])
    return shown, max(len(ordered) - len(shown), 0)


def _priority(asset: Asset) -> tuple[int, str, str]:
    expired_penalty = 1 if asset.status == _EXPIRED_STATUS else 0
    # 空到期日补一个极大值：空串按字典序会排到最前面，等于把「不知道什么时候到期」放到第一
    return (expired_penalty, asset.expires_at or "9999-12-31", asset.id)


def _profile(contact: Contact) -> str:
    # [TUTOR-WHY] 顾问也要带上：换人跟进时销售得知道跟谁对账
    return f"客户：{contact.name}（{contact.id}）；负责顾问：{contact.advisor}"


def _history(contact: Contact) -> str:
    # [TUTOR-WHY] 空日期说「无记录」，不留空白：模型爱把空白补成一句话
    return f"最近互动：{contact.last_interaction_at or '无记录'}"


def _assets(assets: tuple[Asset, ...], omitted: int = 0) -> str:
    # [TUTOR-WHY] 没有资产是有此人但没保单，和「查无此人」必须分开说
    if not assets:
        return "持有资产：无记录"
    lines = [f"持有资产 {len(assets) + omitted} 条："]
    for index, asset in enumerate(assets, start=1):
        lines.append(
            f"{index}. {asset.product}｜状态：{asset.status}｜"
            f"到期：{asset.expires_at or '无记录'}｜年缴：{_yuan(asset.premium_cents)}"
        )
    # [TUTOR-WHY] 裁掉的部分只给条数：说「还有 3 条」能让销售知道要翻库，比假装只有 5 条安全
    if omitted:
        lines.append(f"（另有 {omitted} 条未显示，已按到期日排序）")
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
