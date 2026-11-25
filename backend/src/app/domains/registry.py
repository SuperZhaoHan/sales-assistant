"""domain pack：一个行业的全部可变内容（模板 / 规则 / 语料 / 种子数据）。

换行业 = 换一个目录，不改代码。代价是这些文件必须在启动时校验：
配置里写了不存在的 domain，宁可起不来，也不要用空模板静默跑起来。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.agent.state import Intent
from app.core.errors import ValidationError
from app.llm.prompts.templates import PromptTemplate

DOMAINS_ROOT = Path(__file__).resolve().parent
REQUIRED_FILES = ("domain.yaml", "templates.yaml", "intents.yaml", "rules.yaml")
REQUIRED_DIRS = ("corpus", "seed")

_PACKS: dict[str, DomainPack] = {}


@dataclass(frozen=True, slots=True)
class DomainPack:
    """一个行业包。加载完就固定下来，运行期只读。"""

    name: str
    display_name: str
    locale: str
    root: Path
    templates: Mapping[str, PromptTemplate]
    intents: Mapping[str, tuple[str, ...]]
    rules: Mapping[str, Any]

    @property
    def rule_count(self) -> int:
        """规则条数按阶段数算——这是业务方唯一会天天数的东西。"""
        stages = self.rules.get("stages")
        return len(stages) if isinstance(stages, Mapping) else 0


def _available(base: Path) -> list[str]:
    if not base.is_dir():
        return []
    dirs = [p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))]
    return sorted(dirs)


def _read_yaml(path: Path, domain: str) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"domain {domain} 的 {path.name} 不是合法 YAML：{exc}",
            details={"domain": domain, "file": path.name},
        ) from exc
    if not isinstance(data, Mapping):
        raise ValidationError(
            f"domain {domain} 的 {path.name} 必须是映射结构",
            details={"domain": domain, "file": path.name},
        )
    return data


def _build_template(domain: str, key: str, payload: Any) -> PromptTemplate:
    """把 yaml 里的一个模板条目变成 PromptTemplate，四段校验交给它自己做。"""
    if not isinstance(payload, Mapping):
        raise ValidationError(
            f"domain {domain} 的模板 {key} 必须是映射结构",
            details={"domain": domain, "template": key},
        )
    constraints = payload.get("constraints")
    if not isinstance(constraints, list):
        raise ValidationError(
            f"domain {domain} 的模板 {key} 的 constraints 必须是列表",
            details={"domain": domain, "template": key, "section": "constraints"},
        )
    try:
        return PromptTemplate(
            name=key,
            version=str(payload["version"]),
            role=str(payload["role"]),
            task=str(payload["task"]),
            constraints=tuple(str(item) for item in constraints),
            output_format=str(payload["output_format"]),
            description=str(payload.get("description", "")),
        )
    except KeyError as exc:
        raise ValidationError(
            f"domain {domain} 的模板 {key} 缺少字段 {exc}",
            details={"domain": domain, "template": key, "section": exc.args[0]},
        ) from exc
    except ValidationError as exc:
        raise ValidationError(
            f"domain {domain} 的模板 {key} 不合法：{exc}",
            details={"domain": domain, "template": key},
        ) from exc


def _build_intents(domain: str, raw: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """校验意图词表：key 必须是 Intent 的合法值，且每个意图至少一个关键词。

    这两条都是"静默失效"型错误：意图名打错会让整句话落进闲聊，
    空词表等于这个意图永远不触发——启动时不拦，就只能等线上发现。
    """
    allowed = sorted(intent.value for intent in Intent)
    result: dict[str, tuple[str, ...]] = {}
    for key, keywords in raw.items():
        intent_name = str(key)
        if intent_name not in allowed:
            raise ValidationError(
                f"domain {domain} 的 intents.yaml 里有未知意图：{intent_name}",
                details={"domain": domain, "intent": intent_name, "allowed": allowed},
            )
        if not isinstance(keywords, list):
            raise ValidationError(
                f"domain {domain} 的意图 {intent_name} 的关键词必须是列表",
                details={"domain": domain, "intent": intent_name},
            )
        cleaned = tuple(str(item).strip() for item in keywords if str(item).strip())
        if not cleaned:
            raise ValidationError(
                f"domain {domain} 的意图 {intent_name} 没有任何有效关键词",
                details={"domain": domain, "intent": intent_name},
            )
        result[intent_name] = cleaned
    if not result:
        raise ValidationError(
            f"domain {domain} 的 intents.yaml 是空的，所有消息都会落进闲聊",
            details={"domain": domain, "file": "intents.yaml"},
        )
    return result


def load_domain(name: str, root: Path | None = None) -> DomainPack:
    """加载一个 domain pack；任何缺件、名字不符、YAML 坏了都直接抛。"""
    base = Path(root) if root is not None else DOMAINS_ROOT
    pack_dir = base / name

    if not pack_dir.is_dir():
        raise ValidationError(
            f"未知的 domain：{name}",
            details={"domain": name, "available": _available(base)},
        )

    missing = [f for f in REQUIRED_FILES if not (pack_dir / f).is_file()]
    missing += [d for d in REQUIRED_DIRS if not (pack_dir / d).is_dir()]
    if missing:
        raise ValidationError(
            f"domain {name} 缺少必需文件或目录：{'、'.join(missing)}",
            details={"domain": name, "missing": missing},
        )

    meta = _read_yaml(pack_dir / "domain.yaml", name)
    declared = str(meta.get("name", ""))
    if declared != name:
        raise ValidationError(
            f"domain 目录名与 domain.yaml 里的 name 不一致：目录 {name}，声明 {declared}",
            details={"domain": name, "declared": declared},
        )

    raw_templates = _read_yaml(pack_dir / "templates.yaml", name).get("templates") or {}
    if not isinstance(raw_templates, Mapping):
        raise ValidationError(
            f"domain {name} 的 templates.yaml 里 templates 必须是映射",
            details={"domain": name, "file": "templates.yaml"},
        )
    templates = {key: _build_template(name, key, payload) for key, payload in raw_templates.items()}

    raw_intents = _read_yaml(pack_dir / "intents.yaml", name).get("intents") or {}
    if not isinstance(raw_intents, Mapping):
        raise ValidationError(
            f"domain {name} 的 intents.yaml 里 intents 必须是映射",
            details={"domain": name, "file": "intents.yaml"},
        )
    intents = _build_intents(name, raw_intents)

    rules = _read_yaml(pack_dir / "rules.yaml", name)

    return DomainPack(
        name=name,
        display_name=str(meta.get("display_name", name)),
        locale=str(meta.get("locale", "zh-CN")),
        root=pack_dir,
        templates=templates,
        intents=intents,
        rules=rules,
    )


def get_domain(name: str) -> DomainPack:
    """同一个进程里只加载一次；加载失败不缓存，改完配置重启就能试。"""
    cached = _PACKS.get(name)
    if cached is None:
        cached = load_domain(name)
        _PACKS[name] = cached
    return cached


def reset_cache() -> None:
    """测试用：清掉缓存，模拟进程重启。"""
    _PACKS.clear()
