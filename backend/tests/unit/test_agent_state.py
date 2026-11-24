"""状态与枚举的单元测试。

重点只有一条：外部传来的字符串一旦不在枚举里，必须报错并说清允许哪些值，
不能悄悄变成默认值。
"""

from __future__ import annotations

import pytest

from app.agent.intent import classify_intent
from app.agent.state import Intent, StopReason, TaskStage, parse_enum
from app.core.errors import ValidationError


def test_intent_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_enum(Intent, "book_flight", field_name="intent")

    assert exc_info.value.code == "VALIDATION_1001"
    assert exc_info.value.details["field"] == "intent"
    assert exc_info.value.details["allowed"] == [item.value for item in Intent]


def test_task_stage_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_enum(TaskStage, "paid", field_name="stage")

    assert exc_info.value.code == "VALIDATION_1001"
    assert exc_info.value.details["allowed"] == [
        "draft",
        "awaiting_confirm",
        "committed",
        "aborted",
    ]


def test_enum_values_are_stable_contract() -> None:
    """枚举值是接口契约的一部分，被改掉就等于改了 API，这里钉死。"""

    assert [item.value for item in StopReason] == [
        "done",
        "max_steps",
        "budget_exceeded",
        "timeout",
        "refused",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("帮我建个待办，明天提醒我跟这个客户", Intent.CREATE_TASK),
        ("张总这张保单什么时候到期？", Intent.ASK_CUSTOMER),
        ("犹豫期退保的条款怎么算？", Intent.ASK_KNOWLEDGE),
        ("在吗", Intent.CHITCHAT),
    ],
)
def test_classify_intent(text: str, expected: Intent) -> None:
    assert classify_intent(text) is expected


def test_classify_intent_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        classify_intent("   ")
