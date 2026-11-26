"""日期抽取：把摘要正文里的日期取出来，再对照 facts 判断是不是编的。

为什么要单独成模块：这套判断以后会被两处复用——评测脚本（离线看模型表现）
和上线后的出口校验（正文出现库里没有的日期就拦掉或打标）。放在脚本里就只能被测一次，
放在模块里才有单元测试，也能被别的地方 import。
"""

from __future__ import annotations

import re
from datetime import date

# 2026-03-01 / 2026-3-1
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
# 2026年3月1日 / 2026 年 3 月 1 日（模型在话术里通常写这种）
_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

_SENTENCE_SPLIT = re.compile(r"[。！？\n]")


def normalize(year: str, month: str, day: str) -> str | None:
    """把年月日归一成 ISO；不存在的日期（2 月 30 日）返回 None 而不是报错。

    [TUTOR-WHY] 用 datetime.date 兜底而不是自己做范围判断：闰年、大小月它都管，
    自己写 `month <= 12 and day <= 31` 会把 2 月 30 日放过去。
    """
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def extract_dates(text: str) -> list[tuple[str, str]]:
    """返回 [(正文原样写法, ISO 形式)]，非法日期直接丢弃。"""
    found: list[tuple[str, str]] = []
    for pattern in (_ISO, _CN):
        for match in pattern.finditer(text):
            iso = normalize(*match.groups())
            if iso is not None:
                found.append((match.group(0), iso))
    return found


def ungrounded(text: str, facts: list[str]) -> list[tuple[str, str]]:
    """正文出现、但 facts 里没有的日期 —— 这就是"编造日期"。"""
    known = set(facts)
    return [(raw, iso) for raw, iso in extract_dates(text) if iso not in known]


def sentences_with(text: str, needle: str) -> list[str]:
    """取出包含 needle 的整句：报告里给人看的是句子，不是孤立日期。"""
    parts = _SENTENCE_SPLIT.split(text)
    return [part.strip() for part in parts if needle in part and part.strip()]
