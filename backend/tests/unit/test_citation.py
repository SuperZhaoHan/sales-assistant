"""1.6 验收：摘要里的编号能不能点到真实数据块。

判定只有三种结果，用例也按这三种写：
1. 编号存在 → resolved=True，且 source 就是库里的原值；
2. 编号不存在（模型编的）→ resolved=False，kind="unknown"；
3. 一句话没编号 → 可追溯率为 None，绝不折算成 100%。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.brief import build_brief
from app.agent.citation import build_catalog, resolve_citations
from app.store.sqlite_store import CustomerStore
from app.tools.bootstrap import open_store
from app.tools.registry import reset_registry

SEED_DIR = Path(__file__).resolve().parents[2] / "src/app/domains/insurance/seed"


@pytest.fixture
def store() -> CustomerStore:
    reset_registry()
    return open_store(":memory:", SEED_DIR)


def test_catalog_labels_match_brief_order(store: CustomerStore) -> None:
    """编号顺序必须和事实清单一致：F1 就是 facts[0]，否则反查会指错块。"""
    brief = build_brief(store, "张伟")
    catalog = build_catalog(brief.facts)

    assert catalog["F1"] == ("fact", "F1", brief.facts[0])
    assert brief.fact_list().startswith("F1=")
    assert len(catalog) == len(brief.facts)


def test_known_citations_resolve_to_real_values(store: CustomerStore) -> None:
    brief = build_brief(store, "张伟")
    answer = "【阶段】C 待决策——重疾险 2026-03-01 到期且待续保 [F1][F3]"

    citations, rate = resolve_citations(answer, build_catalog(brief.facts))

    assert rate == 1.0
    assert [c.label for c in citations] == ["[F1]", "[F3]"]
    assert citations[0].source == "2026-03-01"
    assert all(c.resolved for c in citations)


def test_invented_label_is_unresolved(store: CustomerStore) -> None:
    """模型编一个 F9：必须被标成 unknown，而不是"看起来引用了"就算过。"""
    brief = build_brief(store, "张伟")
    answer = "客户很着急 [F1]，预计下月成交 [F9]"

    citations, rate = resolve_citations(answer, build_catalog(brief.facts))

    invented = next(c for c in citations if c.label == "[F9]")
    assert invented.resolved is False
    assert invented.kind == "unknown"
    assert invented.source == ""
    assert rate == 0.5  # 2 条引用里 1 条查不到


def test_duplicate_labels_count_once(store: CustomerStore) -> None:
    # 同一编号重复引用只算一次，否则复读一遍就能刷高分母
    brief = build_brief(store, "张伟")
    answer = "到期 [F1]，再说一次到期 [F1]，再说一次 [F1]"

    citations, rate = resolve_citations(answer, build_catalog(brief.facts))

    assert len(citations) == 1
    assert rate == 1.0


def test_answer_without_citations_has_no_rate(store: CustomerStore) -> None:
    brief = build_brief(store, "张伟")
    citations, rate = resolve_citations("客户快到期了，建议尽快联系。", build_catalog(brief.facts))

    assert citations == ()
    assert rate is None  # 关键：没引用 ≠ 100% 可追溯


def test_knowledge_chunks_get_k_labels() -> None:
    catalog = build_catalog(("2026-03-01",), (("kb-012", "意外险按职业类别定价"),))
    assert catalog["K1"] == ("knowledge", "kb-012", "意外险按职业类别定价")

    citations, rate = resolve_citations("按职业类别定价 [K1]", catalog)
    assert rate == 1.0
    assert citations[0].ref_id == "kb-012"
