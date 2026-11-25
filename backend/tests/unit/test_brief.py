"""1.3a 验收：查档这一步能不能站住。

三条共同逻辑：查不到就不给半成品；有此人没资产说「无记录」；事实清单能用来校验。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.brief import build_brief, not_found_text
from app.store.sqlite_store import CustomerStore
from app.tools.bootstrap import open_store
from app.tools.customer_profile import render
from app.tools.registry import reset_registry

SEED_DIR = Path(__file__).resolve().parents[2] / "src/app/domains/insurance/seed"


@pytest.fixture
def store() -> CustomerStore:
    reset_registry()
    return open_store(":memory:", SEED_DIR)


def test_brief_splits_into_three_variables(store: CustomerStore) -> None:
    brief = build_brief(store, "张伟")
    assert brief.found is True
    # 三段分别对应模板里的 {profile} / {assets} / {history}
    assert brief.profile.startswith("客户：张伟（c-1001）")
    assert "持有资产 2 条" in brief.assets
    assert "到期：2026-03-01" in brief.assets
    assert "状态：待续保" in brief.assets
    assert brief.history == "最近互动：2026-11-18"
    assert set(brief.variables()) == {"profile", "assets", "history"}


def test_facts_cover_expiry_status_and_interaction(store: CustomerStore) -> None:
    brief = build_brief(store, "张伟")
    # 校验用的原文必须齐：两条到期日 + 两种状态 + 最近互动
    assert "2026-03-01" in brief.facts
    assert "待续保" in brief.facts
    assert "2026-11-18" in brief.facts
    assert len(brief.facts) == len(set(brief.facts))  # 去重过


def test_customer_without_assets_has_only_one_fact(store: CustomerStore) -> None:
    brief = build_brief(store, "王芳")
    assert brief.found is True
    assert brief.assets == "持有资产：无记录"
    # 没有保单 → 事实清单里只剩「最近互动」，绝无编造出来的到期日
    assert brief.facts == ("2026-09-02",)


def test_unknown_customer_returns_empty_brief(store: CustomerStore) -> None:
    brief = build_brief(store, "张三")
    assert brief.found is False
    assert brief.variables() == {"profile": "", "assets": "", "history": ""}
    assert brief.facts == ()
    assert "未找到客户：张三" in not_found_text("张三")


def test_brief_and_tool_use_the_same_numbers(store: CustomerStore) -> None:
    """同一份数据、两条渲染路径，数字必须一致 —— 防两处格式化各改各的。"""
    contact = store.find_contact("张伟")
    assert contact is not None
    assets = store.list_assets(contact.id)
    tool_text = render(customer_name="张伟", contact=contact, assets=assets)
    brief = build_brief(store, "张伟")
    for token in ("2026-03-01", "待续保", "12,800 元"):
        assert token in tool_text
        assert token in brief.assets
