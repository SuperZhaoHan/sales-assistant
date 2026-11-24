"""意图识别：先规则，后模型。

阶段 0 只用关键词规则，因为现在要先把「意图 → 工具 → 答复」这条链路测通；
接真模型后加一层兜底，规则命不中才调模型——顺序不能反，
反了就会变成「每句话都花一次 token 去判断意图」。

规则的顺序有意义：一条消息里同时出现「建个待办」和「这个客户」时，
应按创建任务处理，所以 ``create_task`` 排在最前面。
"""

from __future__ import annotations

from app.agent.state import Intent
from app.core.errors import ValidationError

INTENT_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.CREATE_TASK: ("建个待办", "创建任务", "提醒我", "安排一下", "记一下"),
    Intent.ASK_CUSTOMER: ("客户", "线索", "什么阶段", "到期", "保单", "资产", "跟进到哪"),
    Intent.ASK_KNOWLEDGE: ("条款", "理赔", "怎么算", "知识", "流程", "话术", "什么意思"),
}


def classify_intent(text: str) -> Intent:
    """返回意图；空文本直接报错，不返回 ``chitchat``。

    空消息如果归成闲聊，会静默消耗一次模型调用，而且日志里看不出异常。
    """

    normalized = text.strip()
    if not normalized:
        raise ValidationError("任务描述不能为空", details={"field": "task"})

    for intent, keywords in INTENT_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return intent
    return Intent.CHITCHAT
