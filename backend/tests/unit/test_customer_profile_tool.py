"""1.2 验收：查得到、字段齐、已脱敏、库里没有明文。

这一批用例的共同逻辑是「少一块就红」：
- 去掉入库时的 mask_pii → 库里有明文那条红；
- 输出里漏掉 status 或 expires_at → 字段齐那条红；
- 把「未找到」写成「无记录」→ 区分两种空值那条红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.planner import FakeLLMPlanner
from app.core.errors import ValidationError
from app.store.sqlite_store import CustomerStore
from app.tools.bootstrap import open_store, register_domain_tools
from app.tools.customer_profile import build_customer_profile_tool
from app.tools.registry import list_tools, reset_registry

SEED_DIR = Path(__file__).resolve().parents[2] / "src/app/domains/insurance/seed"
PLAINTEXT_PHONE = "13812348000"
PLAINTEXT_ID = "110101199001011234"


@pytest.fixture
def store() -> CustomerStore:
    reset_registry()
    return open_store(":memory:", SEED_DIR)


@pytest.fixture
def tool(store: CustomerStore):
    return build_customer_profile_tool(store)


def test_seed_rows_loaded_and_idempotent(store: CustomerStore) -> None:
    assert store.contact_count() == 4
    assert len(store.list_assets("c-1001")) == 2
    # 再灌一次不该报错，也不该多出行：重启、重跑脚本都走这条路径
    assert store.load_seed(SEED_DIR) == (4, 5)
    assert store.contact_count() == 4


def test_no_plaintext_in_tables(store: CustomerStore) -> None:
    phones = [row["phone"] for row in store.raw_rows("contacts")]
    id_cards = [row["id_card"] for row in store.raw_rows("contacts")]
    assert PLAINTEXT_PHONE not in " ".join(phones)
    assert PLAINTEXT_ID not in " ".join(id_cards)
    assert all(phone == "[PHONE]" for phone in phones)


async def test_output_has_expiry_status_and_interaction(tool) -> None:
    text = await tool.run({"customer_name": "张伟"})
    assert "到期：2026-03-01" in text
    assert "状态：待续保" in text
    assert "最近互动：2026-11-18" in text
    assert "持有资产 2 条" in text


async def test_output_has_no_plaintext(tool) -> None:
    text = await tool.run({"customer_name": "张伟"})
    assert PLAINTEXT_PHONE not in text
    assert PLAINTEXT_ID not in text


async def test_unknown_customer_says_not_found_without_listing_others(tool) -> None:
    text = await tool.run({"customer_name": "张三"})
    assert "未找到客户：张三" in text
    assert "李娜" not in text and "陈建国" not in text


async def test_customer_without_assets_says_no_record(tool) -> None:
    text = await tool.run({"customer_name": "王芳"})
    assert "持有资产：无记录" in text
    assert "未找到" not in text


async def test_extra_arg_rejected(tool) -> None:
    # 模型幻觉多塞一个字段时必须炸，不能让「按租户过滤」变成一句空话
    with pytest.raises(ValidationError) as caught:
        await tool.run({"customer_name": "张伟", "tenant_id": "t-999"})
    assert "tenant_id" in str(caught.value)


async def test_empty_name_rejected(tool) -> None:
    with pytest.raises(ValidationError):
        await tool.run({"customer_name": ""})


def test_registered_tools_are_known_to_planner(store: CustomerStore) -> None:
    names = register_domain_tools(store)
    assert names == ("customer_profile",)
    # 反向约束：注册了却没人会调的工具，等于挂了一块牌子
    assert set(names).issubset(set(FakeLLMPlanner.actions))
    assert [tool.name for tool in list_tools()] == ["customer_profile"]
