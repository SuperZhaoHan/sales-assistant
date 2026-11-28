"""1.5 验收（工具层）：返回带编号与 chunk_id 的证据块；没命中就说没有。

可失败点：
- 去掉 ``render`` 里的 chunk_id → test_returns_citation_anchor 红；
- 把 min_score 当成 0 → test_irrelevant_question_says_no_answer 红（会返回一堆低分段落，
  模型会当证据用）；
- 去掉 ``top_k`` 的 ``le=5`` 上限 → test_top_k_overflow_rejected 红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.domains import load_domain
from app.retrieval.retriever import Retriever
from app.tools.bootstrap import build_retriever
from app.tools.knowledge_search import build_knowledge_search_tool
from app.tools.registry import list_tools, reset_registry

PACK_ROOT = Path(__file__).resolve().parents[2] / "src/app/domains/insurance"


@pytest.fixture
def retriever() -> Retriever:
    return build_retriever(PACK_ROOT / "corpus")


@pytest.fixture
def tool(retriever: Retriever):
    reset_registry()
    return build_knowledge_search_tool(retriever, min_score=0.35)


async def test_returns_citation_anchor(tool) -> None:
    text = await tool.run({"query": "百万医疗的免赔额是多少"})
    assert "[1]" in text
    assert "kb-003" in text
    # 引用编号与 chunk_id 必须同时在，否则 1.6 的「反查」无从谈起
    assert "相关度" in text


async def test_irrelevant_question_says_no_answer(tool) -> None:
    text = await tool.run({"query": "你们家去年理赔率是多少"})
    assert "未找到" in text
    assert "不要凭常识作答" in text
    # 不许把低分段落硬塞给模型
    assert "kb-" not in text


async def test_empty_query_rejected(tool) -> None:
    with pytest.raises(ValidationError):
        await tool.run({"query": "  "})


async def test_top_k_overflow_rejected(tool) -> None:
    with pytest.raises(ValidationError) as caught:
        await tool.run({"query": "理赔材料", "top_k": 9})
    assert "top_k" in str(caught.value)


def test_all_domain_tools_registered(retriever: Retriever) -> None:
    pack = load_domain("insurance")
    from app.tools.bootstrap import open_store, register_domain_tools

    store = open_store(":memory:", pack.root / "seed")
    names = register_domain_tools(pack, store, retriever)
    assert names == ("customer_profile", "knowledge_search", "create_task", "delete_task")
    assert len(list_tools()) == 4
