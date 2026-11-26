"""1.7 验收：拒答。

三条：
1. 拒答话术来自行业包（换行业要能改），代码只负责填 query 与阈值；
2. 该拒的 7 道题，顶分全在阈值以下 —— 这是拒答正确率 100% 的来源；
3. 已知缺陷：哈希 embedding 的分数不可比，阈值 0.35 会误拒 5 道该答题。
   用 xfail(strict=True) 钉住它：哪天 embedding 换好了，这条会红，逼我们回来重扫阈值。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.domains import load_domain
from app.retrieval.retriever import Retriever
from app.tools.bootstrap import build_retriever, min_score_from, refusal_text_from
from app.tools.knowledge_search import build_knowledge_search_tool
from app.tools.registry import reset_registry

PACK_ROOT = Path(__file__).resolve().parents[2] / "src/app/domains/insurance"


@pytest.fixture
def pack():
    return load_domain("insurance")


@pytest.fixture
def retriever() -> Retriever:
    reset_registry()
    return build_retriever(PACK_ROOT / "corpus")


def _gold(pack) -> list[dict]:
    data = yaml.safe_load((pack.root / "eval" / "gold.yaml").read_text(encoding="utf-8"))
    return list(data["questions"])


def test_pack_declares_refusal_text(pack) -> None:
    text = refusal_text_from(pack)
    assert text is not None
    # 两个占位符缺一不可：模型要知道「查的是什么」和「为什么被拒」
    assert "{query}" in text
    assert "{min_score}" in text


async def test_tool_uses_pack_refusal_text(pack, retriever) -> None:
    tool = build_knowledge_search_tool(
        retriever,
        min_score=min_score_from(pack),
        refusal_text=refusal_text_from(pack),
    )
    text = await tool.run({"query": "别家同类产品比你们便宜多少"})

    assert "未找到" in text
    assert "别家同类产品比你们便宜多少" in text  # query 被填进去了
    assert "0.35" in text  # 阈值也被填进去了
    assert "人工确认" in text
    # 拒答时绝不能把低分片段当证据塞给模型
    assert "kb-" not in text


def test_every_unanswerable_question_is_below_threshold(pack, retriever) -> None:
    """该拒的每道题，顶分都必须在阈值下 —— 这是 7/7 的机制，不是巧合。"""
    min_score = min_score_from(pack)
    unanswerable = [row for row in _gold(pack) if not row["expect"]]
    assert len(unanswerable) == 7

    for row in unanswerable:
        hits = retriever.search(row["q"], top_k=3)
        top1 = hits[0] if hits else None
        assert top1 is None or top1.score < min_score, f"这题顶分过高：{row['q']}"


def test_threshold_currently_false_refuses_a_correct_retrieval(pack, retriever) -> None:
    """轻症那题其实**检索对了**（top1 就是 kb-008），但分数 0.24 被阈值挡掉。

    这条锁住「已知缺陷」的现状，不是断言「这样是对的」：
    哪天 embedding 换好、分数上去了，assert 会红 —— 那时才该回来重扫阈值并改这段说明。
    """
    min_score = min_score_from(pack)
    hits = retriever.search("轻症一般赔保额的多少？", top_k=3)
    assert hits and hits[0].chunk.id == "kb-008"  # 检索没毛病
    assert hits[0].score < min_score  # 但分数不够，会被拒（缺陷在本行：分数不可比）
