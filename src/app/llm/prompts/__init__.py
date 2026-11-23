"""Versioned prompt templates.

Keep prompt text out of business logic. Load prompts by name and version so
changes are reviewable, testable and auditable.

Exports:
    PromptTemplate  四段式模板（角色 / 任务 / 约束 / 输出格式）
    get_template    按 name + version 取模板（version 省略取最新）
    list_templates  列出已注册模板
    register        注册新模板（同名同版本不可覆盖）
"""
from app.llm.prompts.templates import (
    PromptTemplate,
    get_template,
    list_templates,
    register,
)

__all__ = ["PromptTemplate", "get_template", "list_templates", "register"]
