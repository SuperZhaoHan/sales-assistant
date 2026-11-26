"""1.10 验收：阈值可由配置覆盖，且覆盖优先级高于行业包。

为什么值得一条用例：阈值跟 embedding 绑定（换尺子分数整体抬高），
如果配置不生效，就会出现「换了 bge-m3 却还在用 0.35 判」，线上悄悄答了不该答的题。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domains import load_domain
from app.tools.bootstrap import build_retriever, min_score_from
from app.tools.knowledge_search import build_knowledge_search_tool

PACK_ROOT = Path(__file__).resolve().parents[2] / "src/app/domains/insurance"


@pytest.fixture
def pack():
    return load_domain("insurance")


def test_pack_value_used_when_no_override(pack) -> None:
    # 行业包里的 0.35 是哈希基线，不覆盖时必须原样生效
    assert min_score_from(pack) == pytest.approx(0.35)
    assert min_score_from(pack, None) == pytest.approx(0.35)


def test_override_wins_over_pack(pack) -> None:
    assert min_score_from(pack, 0.64) == pytest.approx(0.64)


async def test_override_reaches_the_tool(pack) -> None:
    """覆盖必须能真的走到工具：同一道题在 0.35 下作答、在 0.64 下改口。

    用哈希向量（CI 不联网）当「坏尺子」：这条题 top1 = 0.44，
    正好卡在两个阈值中间——阈值没传下去，这条会红。
    """
    retriever = build_retriever(pack.root / "corpus")
    loose = build_knowledge_search_tool(retriever, min_score=min_score_from(pack))
    strict = build_knowledge_search_tool(retriever, min_score=min_score_from(pack, 0.64))

    assert "kb-003" in await loose.run({"query": "百万医疗的免赔额是多少？"})
    assert "未找到" in await strict.run({"query": "百万医疗的免赔额是多少？"})
