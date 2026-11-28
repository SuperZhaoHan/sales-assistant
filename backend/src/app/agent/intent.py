"""意图识别：先规则，后模型。

阶段 0 只用关键词规则，因为现在要先把「意图 → 工具 → 答复」这条链路测通；
接真模型后加一层兜底，规则命不中才调模型——顺序不能反，
反了就会变成「每句话都花一次 token 去判断意图」。

规则的顺序有意义：一条消息里同时出现「建个待办」和「这个客户」时，
应按创建任务处理，所以 ``create_task`` 排在最前面。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.agent.state import Intent
from app.core.errors import ValidationError

INTENT_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    # 「不用提醒我了」里含「提醒我」，所以删除类必须排在创建前面
    Intent.DELETE_TASK: ("删了", "删掉", "删除", "撤销", "取消待办", "不用提醒"),
    Intent.CREATE_TASK: ("建个待办", "创建任务", "提醒我", "安排一下", "记一下"),
    Intent.ASK_CUSTOMER: ("客户", "线索", "什么阶段", "到期", "保单", "资产", "跟进到哪"),
    Intent.ASK_KNOWLEDGE: ("条款", "理赔", "怎么算", "知识", "流程", "话术", "什么意思"),
}


def classify_intent(
    text: str,
    intents: Mapping[str, tuple[str, ...]] | None = None,
) -> Intent:
    """返回意图；空文本直接报错，不返回 ``chitchat``。

    空消息如果归成闲聊，会静默消耗一次模型调用，而且日志里看不出异常。

    ``intents`` 传 domain pack 的词表（``pack.intents``）；
    不传时用下面这份保险默认词表，只为兼容老调用方与单测。
    运行时必须传 pack 的——换行业零代码，靠的就是这一层注入。
    """

    normalized = text.strip()
    if not normalized:
        raise ValidationError("任务描述不能为空", details={"field": "task"})

    table = intents if intents is not None else INTENT_KEYWORDS
    for name, keywords in table.items():
        if any(keyword in normalized for keyword in keywords):
            return Intent(name)
    return Intent.CHITCHAT
