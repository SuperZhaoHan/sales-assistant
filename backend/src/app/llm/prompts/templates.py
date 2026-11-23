"""四段式提示词模板：角色 / 任务 / 约束 / 输出格式。

为什么固定四段：
1. 角色 —— 决定模型站在谁的立场说话（销售助手 vs 客服 vs 客户本人）；
2. 任务 —— 决定它做什么，避免模型把「判断阶段」写成「写一篇分析」；
3. 约束 —— 决定它不能做什么（编造客户信息、乱承诺折扣、越权执行写操作），
   这是企业场景里最容易出事的一段，必须显式列成条目，便于逐条 review；
4. 输出格式 —— 决定结果长什么样，让下游能解析、让销售能直接用。

工程约束：
- 模板与业务代码分离，按 name + version 存取，改动走 code review，可回滚；
- 模板里的占位符用 ``{变量名}``，由调用方传入 variables 渲染；
- 缺少变量、模板不存在都抛 ValidationError（对外返回 VALIDATION_1001），
  不允许静默渲染出半成品提示词。

典型用法::

    from app.llm.prompts import get_template

    system = get_template("sales_followup").render(
        {"profile": "客户张总，制造业，已试用 5 天", "history": "上周问过价格，未报价"}
    )
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.core.errors import ValidationError

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """四段式模板。四段缺一不可，构造时校验。"""

    name: str
    version: str
    role: str
    task: str
    constraints: tuple[str, ...]
    output_format: str
    description: str = field(default="")

    def __post_init__(self) -> None:
        sections = (
            ("role", self.role),
            ("task", self.task),
            ("output_format", self.output_format),
        )
        for label, value in sections:
            if not value.strip():
                raise ValidationError(
                    f"prompt template {self.name}{self.version} 缺少 {label}",
                    details={"template": self.name, "version": self.version, "section": label},
                )
        if not self.constraints:
            raise ValidationError(
                f"prompt template {self.name}{self.version} 缺少 constraints",
                details={"template": self.name, "version": self.version, "section": "constraints"},
            )

    @property
    def variables(self) -> tuple[str, ...]:
        """模板声明的占位符变量（去重、按出现顺序）。"""
        found: list[str] = []
        for text in self._all_text():
            for var in _PLACEHOLDER.findall(text):
                if var not in found:
                    found.append(var)
        return tuple(found)

    def render(self, variables: Mapping[str, str] | None = None) -> str:
        """把四段拼成一段 system prompt，并替换占位符。

        缺少变量直接报错，不渲染半成品——半成品提示词比报错更难排查。
        """
        variables = dict(variables or {})
        missing = [v for v in self.variables if v not in variables]

        def _sub(text: str) -> str:
            for var in self.variables:
                if var in variables:
                    text = text.replace("{" + var + "}", str(variables[var]))
            return text

        if missing:
            raise ValidationError(
                "提示词模板缺少变量：" + ", ".join(missing),
                details={
                    "template": self.name,
                    "version": self.version,
                    "missing": missing,
                    "required": list(self.variables),
                },
            )

        lines: list[str] = [
            "【角色】",
            _sub(self.role).strip(),
            "",
            "【任务】",
            _sub(self.task).strip(),
            "",
            "【约束】",
        ]
        for index, item in enumerate(self.constraints, start=1):
            lines.append(f"{index}. {_sub(item).strip()}")
        lines += ["", "【输出格式】", _sub(self.output_format).strip()]
        return "\n".join(lines)

    def _all_text(self) -> tuple[str, ...]:
        return (self.role, self.task, *self.constraints, self.output_format)

    def fingerprint(self) -> dict[str, str]:
        """把模板身份带进日志与审计，事后能还原「这条回答是哪版提示词产出的」。"""
        return {"name": self.name, "version": self.version}


SALES_FOLLOWUP_V1 = PromptTemplate(
    name="sales_followup",
    version="v1",
    description="企微私域销售跟进：判断客户阶段、给下一步动作、给可发送话术",
    role=(
        "你是某企业的企微私域销售助手，服务的对象是一线销售，不是你自己的客户。"
        "你的判断必须基于销售提供的客户资料与历史互动记录，不臆测、不替客户表态。"
    ),
    task=(
        "阅读客户资料「{profile}」与最近互动「{history}」，判断该客户当前所处的跟进阶段，"
        "指出最值得推进的一件事，并给出一条可以直接发给客户的话术。"
    ),
    constraints=(
        "只能引用「{profile}」「{history}」中出现的信息；资料不足时，明确说明缺哪一项，不得补编客户信息。",
        "不得承诺未授权的价格、折扣、账期或交付时间；涉及报价一律指向公开价目表，由销售自行确认。",
        "下单、修改客户资料、预约时间属于写操作：只能给出建议动作，不得声称已经执行。",
        "不得输出手机号、身份证号等敏感信息的明文，必要时使用掩码。",
        "话术不超过 80 字，不使用「保证」「绝对」「一定能」这类承诺性措辞。",
    ),
    output_format=(
        "按以下三段输出，标题原样保留：\n"
        "【阶段判断】一句话说明客户所处阶段与依据。\n"
        "【建议动作】不超过两条，每条一句话，并标明是读操作还是写操作。\n"
        "【话术】一条可直接发送的微信话术，不超过 80 字。"
    ),
)


_TEMPLATES: dict[tuple[str, str], PromptTemplate] = {
    (SALES_FOLLOWUP_V1.name, SALES_FOLLOWUP_V1.version): SALES_FOLLOWUP_V1,
}


def register(template: PromptTemplate) -> PromptTemplate:
    """注册模板；同名同版本重复注册视为错误，避免线上版本被悄悄覆盖。"""
    key = (template.name, template.version)
    if key in _TEMPLATES:
        raise ValidationError(
            f"提示词模板已存在：{template.name}{template.version}",
            details={"template": template.name, "version": template.version},
        )
    _TEMPLATES[key] = template
    return template


def list_templates() -> tuple[PromptTemplate, ...]:
    return tuple(_TEMPLATES.values())


def get_template(name: str, version: str | None = None) -> PromptTemplate:
    """按 name 取模板；version 为空时取该 name 下的最新版本。

    版本号按 v1、v2… 的数字部分排序，不使用字符串字典序（否则 v10 会排在 v2 前）。
    """
    candidates = [t for (tname, _), t in _TEMPLATES.items() if tname == name]
    if not candidates:
        raise ValidationError(
            f"未知的提示词模板：{name}",
            details={"template": name, "available": sorted({t.name for t in _TEMPLATES.values()})},
        )
    if version is None:
        return max(candidates, key=lambda t: _version_key(t.version))
    for template in candidates:
        if template.version == version:
            return template
    raise ValidationError(
        f"提示词模板不存在该版本：{name}{version}",
        details={
            "template": name,
            "version": version,
            "available": [t.version for t in candidates],
        },
    )


def _version_key(version: str) -> int:
    digits = re.sub(r"\D", "", version)
    return int(digits) if digits else 0
