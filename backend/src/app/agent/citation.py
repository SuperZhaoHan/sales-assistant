"""引用反查：把摘要里的 ``[F1]`` / ``[K1]`` 标记解析回真实数据块。

为什么编号必须由后端发，而不是让模型自己写：
- 模型编一个 ``[1]`` 是零成本的，编一个能对上库的编号才有意义；
- 编号 → 原文 的对照表留在后端，解析时按表查，查不到就是 ``resolved=False``；
- 这才能算出「可追溯率」这个数字，而不是靠人眼看「看起来引用了」。

两种编号：
- ``F<n>``：客户档案事实清单的第 n 条（到期日/状态/最近互动），来源是 SQLite；
- ``K<n>``：本次检索片段（知识库）的第 n 条，来源是向量索引。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# [F1] / [K12]：只认大写单字母 + 数字，避免把正文里的 [1] 之类误判成引用
CITATION_PATTERN = re.compile(r"\[([FK])(\d+)]")


@dataclass(frozen=True, slots=True)
class Citation:
    """一条引用。``resolved=False`` 时 ``source`` 为空 —— 说明这个编号后端发过没有。"""

    label: str
    kind: str
    ref_id: str
    source: str
    resolved: bool


def build_catalog(
    facts: tuple[str, ...],
    chunks: tuple[tuple[str, str], ...] = (),
) -> dict[str, tuple[str, str, str]]:
    """编号 → (kind, ref_id, 原文)。``chunks`` 是 ``(chunk_id, text)`` 的有序列表。"""
    catalog: dict[str, tuple[str, str, str]] = {}
    for index, value in enumerate(facts, start=1):
        # [TUTOR-WHY] 事实的 ref_id 用序号：facts 里没有主键，取「第几条」最稳，也不泄露库结构
        catalog[f"F{index}"] = ("fact", f"F{index}", value)
    for index, (chunk_id, text) in enumerate(chunks, start=1):
        catalog[f"K{index}"] = ("knowledge", chunk_id, text)
    return catalog


def resolve_citations(
    answer: str,
    catalog: dict[str, tuple[str, str, str]],
) -> tuple[tuple[Citation, ...], float | None]:
    """解析答案里出现过的编号，返回（引用列表，可追溯率）。

    可追溯率的分母是**答案里出现过的引用**，不是全集：
    - 一条引用都没有 → 返回 ``None``，不是 100%。把「没引用」算成满分是自欺。
    - 同一编号出现多次只算一次，否则模型复读一遍就能刷高分母。
    """
    seen: list[str] = []
    for match in CITATION_PATTERN.finditer(answer):
        label = match.group(1) + match.group(2)
        if label not in seen:
            seen.append(label)

    citations: list[Citation] = []
    for label in seen:
        entry = catalog.get(label)
        if entry is None:
            citations.append(
                Citation(label=f"[{label}]", kind="unknown", ref_id="", source="", resolved=False)
            )
            continue
        kind, ref_id, source = entry
        citations.append(
            Citation(label=f"[{label}]", kind=kind, ref_id=ref_id, source=source, resolved=True)
        )

    if not citations:
        return (), None
    resolved = sum(1 for citation in citations if citation.resolved)
    return tuple(citations), round(resolved / len(citations), 4)
