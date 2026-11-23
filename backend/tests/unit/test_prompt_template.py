"""四段式提示词模板的单元测试。"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.llm.prompts import (
    PromptTemplate,
    get_template,
    list_templates,
    register,
)

VARS = {"profile": "客户张总，制造业，已试用 5 天", "history": "上周问过价格，未报价"}


def _make(version: str, name: str = "latest_probe") -> PromptTemplate:
    return PromptTemplate(
        name=name,
        version=version,
        role=f"r{version}",
        task=f"t{version}",
        constraints=(f"c{version}",),
        output_format=f"o{version}",
    )


def test_render_contains_four_sections() -> None:
    text = get_template("sales_followup").render(VARS)

    for label in ("【角色】", "【任务】", "【约束】", "【输出格式】"):
        assert label in text
    assert VARS["profile"] in text
    assert VARS["history"] in text


def test_variables_declared_in_order() -> None:
    template = get_template("sales_followup")

    assert template.variables == ("profile", "history")


def test_missing_variable_raises() -> None:
    with pytest.raises(ValidationError) as excinfo:
        get_template("sales_followup").render({"profile": "只有资料"})

    assert "history" in excinfo.value.details["missing"]
    assert excinfo.value.code == "VALIDATION_1001"


def test_unknown_template_raises() -> None:
    with pytest.raises(ValidationError) as excinfo:
        get_template("no_such_template")

    assert excinfo.value.details["template"] == "no_such_template"
    assert "sales_followup" in excinfo.value.details["available"]


def test_unknown_version_raises() -> None:
    with pytest.raises(ValidationError):
        get_template("sales_followup", "v9")


def test_default_version_is_latest() -> None:
    register(_make("v2"))
    register(_make("v10"))

    # 按数字排序，不是字符串排序（否则 v2 会排在 v10 之后）
    assert get_template("latest_probe").version == "v10"
    assert get_template("latest_probe", "v2").version == "v2"


def test_duplicate_register_raises() -> None:
    template = list_templates()[0]

    with pytest.raises(ValidationError):
        register(template)


def test_empty_section_raises() -> None:
    with pytest.raises(ValidationError):
        PromptTemplate(
            name="bad",
            version="v1",
            role=" ",
            task="t",
            constraints=("c",),
            output_format="o",
        )
