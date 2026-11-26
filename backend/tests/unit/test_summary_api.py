"""1.3b 的入口：查档 → 填模板 → 调模型；查不到就不调。

两条必须守住的规则：
1. 查到了，system prompt 里必须真的带上三段变量（不能空着调模型）；
2. 查不到，token 必须是 0、llm_called 必须是 False（不能拿空资料去问模型）。
"""

from __future__ import annotations

from app.agent.brief import build_brief
from app.agent.summary import build_summary_request
from app.domains import load_domain
from app.store.sqlite_store import CustomerStore


def _store() -> CustomerStore:
    pack = load_domain("insurance")
    store = CustomerStore(":memory:").open()
    store.load_seed(pack.root / "seed")
    return store


def _request_for(name: str):
    pack = load_domain("insurance")
    store = _store()
    brief = build_brief(store, name)
    return pack, build_summary_request(pack, brief, model="fake-model", max_tokens=512)


def test_request_carries_three_variables_into_entry_template() -> None:
    pack, request = _request_for("张伟")
    assert request is not None
    assert request.system is not None
    # 三段变量的内容必须真的落在 system 里，而不是只把占位符替换成空串
    assert "张伟" in request.system
    assert "持有资产" in request.system
    assert "最近互动：2026-11-18" in request.system
    # 占位符必须全部被替换：留一个 { 就说明变量名对不上
    assert "{" not in request.system
    assert request.metadata["prompt_template"] == pack.entry.fingerprint()


def test_unknown_customer_builds_no_request() -> None:
    # 查无此人时返回 None —— 这是「不调模型」的唯一闸门
    _, request = _request_for("张三")
    assert request is None


def test_summary_endpoint_is_wired_to_entry_template(client) -> None:
    response = client.post("/v1/summary", json={"customer_name": "张伟"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["found"] is True
    assert data["llm_called"] is True
    assert data["prompt_template"] == {"name": "insurance_summary", "version": "v1"}
    assert data["usage"]["total_tokens"] > 0
    # 事实清单必须带出来：1.6 的引用反查要用它
    assert "2026-11-18" in data["facts"]
    # fake provider 不产引用 → 可追溯率必须是 None，而不是假装 100%
    assert data["citations"] == []
    assert data["traceable_rate"] is None


def test_summary_endpoint_unknown_customer_costs_zero(client) -> None:
    response = client.post("/v1/summary", json={"customer_name": "张三"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["found"] is False
    assert data["llm_called"] is False
    assert data["usage"]["total_tokens"] == 0
    assert data["usage"]["cost_usd"] == 0.0
    assert data["prompt_template"] is None
    assert data["citations"] == []
    assert data["traceable_rate"] is None
    assert "未找到客户：张三" in data["text"]


def test_summary_endpoint_rejects_empty_name(client) -> None:
    # extra=forbid + min_length：空名字不该走到查库那一步。
    # 全项目统一把请求校验失败映射成 400 + VALIDATION_1001，不是 FastAPI 默认的 422。
    response = client.post("/v1/summary", json={"customer_name": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_1001"
