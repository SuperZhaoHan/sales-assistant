"""1.4 验收：真相源 + 上下文裁剪。

要守住的只有一句话：**事实每次现查，库变了答案就该变**。
所以这里的用例都是「先查一次 → 改库 → 再查一次 → 断言新的」。
只要谁在中间加了缓存，第二条断言就会红 —— 这就是可失败用例的意义。

另一半是裁剪：最多 5 条资产进提示词，被裁掉的既不出现在文本里，也不进 facts。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.brief import MAX_ASSETS_IN_PROMPT, build_brief, split_assets
from app.store.sqlite_store import Asset, CustomerStore
from app.tools.bootstrap import open_store
from app.tools.registry import reset_registry

SEED_DIR = Path(__file__).resolve().parents[2] / "src/app/domains/insurance/seed"


@pytest.fixture
def store() -> CustomerStore:
    reset_registry()
    return open_store(":memory:", SEED_DIR)


def _asset(index: int, *, status: str = "待续保", expires: str = "") -> Asset:
    return Asset(
        id=f"a-test-{index:03d}",
        contact_id="c-1001",
        product=f"测试产品{index}",
        status=status,
        expires_at=expires,
        premium_cents=100_000,
    )


def test_new_interaction_shows_up_without_restart(store: CustomerStore) -> None:
    """改库后再问：答案必须是新值，旧值必须消失。"""
    before = build_brief(store, "张伟")
    assert before.history == "最近互动：2026-11-18"
    assert "2026-11-18" in before.facts

    store.record_interaction("c-1001", "2026-11-25")

    after = build_brief(store, "张伟")
    assert after.history == "最近互动：2026-11-25"
    assert "2026-11-25" in after.facts
    # 旧值必须彻底消失：只要它还留着，就说明有某层缓存了旧数据
    assert "2026-11-18" not in after.facts


def test_asset_status_change_is_visible_in_prompt_and_facts(store: CustomerStore) -> None:
    store.set_asset_status("a-2001", "已续保")

    brief = build_brief(store, "张伟")
    assert "状态：已续保" in brief.assets
    assert "已续保" in brief.facts
    # 改完之后旧的「待续保」不该再出现在可校验清单里
    assert "待续保" not in brief.facts


def test_update_on_missing_row_fails_loudly(store: CustomerStore) -> None:
    # 改了不存在的行必须报错：静默成功会让「改库」这一步看起来对、其实没改
    with pytest.raises(Exception) as excinfo:
        store.record_interaction("c-9999", "2026-11-25")
    assert "不存在" in str(excinfo.value)


def test_prompt_keeps_at_most_five_assets_and_reports_the_rest() -> None:
    assets = tuple(_asset(i, expires=f"2026-{i:02d}-01") for i in range(1, 9))
    shown, omitted = split_assets(assets)

    assert len(shown) == MAX_ASSETS_IN_PROMPT == 5
    assert omitted == 3
    # 展示的是到期最近的那 5 条，不是随机 5 条
    assert [asset.expires_at for asset in shown] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
        "2026-04-01",
        "2026-05-01",
    ]


def test_expired_asset_gives_up_its_seat() -> None:
    """失效件排最后：8 条里混一条失效的，被裁掉的必须是它。"""
    live = tuple(_asset(i, expires=f"2026-{i:02d}-01") for i in range(1, 8))
    expired = _asset(99, status="已失效", expires="2025-01-01")
    shown, omitted = split_assets((*live, expired))

    assert omitted == 3
    assert all(asset.status != "已失效" for asset in shown)


def test_empty_expiry_sorts_last() -> None:
    # 空到期日不能因为字典序排到最前面，否则「不知道何时到期」会抢占第一眼
    assets = tuple(_asset(i, expires=f"2026-{i:02d}-01") for i in range(1, 6)) + (
        _asset(90, expires=""),
    )
    shown, _ = split_assets(assets)
    assert shown[-1].expires_at == "2026-05-01"
    assert all(asset.expires_at for asset in shown)


def test_summary_endpoint_reads_fresh_data(client) -> None:
    """端到端：同一个进程里改库，下一次 HTTP 请求就要看到新值。"""
    before = client.post("/v1/summary", json={"customer_name": "张伟"}).json()["data"]
    assert "2026-11-18" in before["facts"]

    client.app.state.store.record_interaction("c-1001", "2026-11-25")

    after = client.post("/v1/summary", json={"customer_name": "张伟"}).json()["data"]
    assert "2026-11-25" in after["facts"]
    assert "2026-11-18" not in after["facts"]
