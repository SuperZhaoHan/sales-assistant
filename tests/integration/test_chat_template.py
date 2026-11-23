"""模板化对话的集成测试：模板渲染、耗时与费用字段、错误路径。"""
from __future__ import annotations

from fastapi.testclient import TestClient


VARS = {"profile": "客户张总，制造业，已试用 5 天", "history": "上周问过价格，未报价"}


def template_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "messages": [{"role": "user", "content": "下一步怎么跟？"}],
        "subject_id": "user-1",
        "tenant_id": "tenant-1",
        "template_name": "sales_followup",
        "template_vars": VARS,
    }
    payload.update(overrides)
    return payload


def test_chat_with_template_returns_latency_and_cost(client: TestClient) -> None:
    response = client.post("/v1/chat", json=template_payload())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["prompt_template"] == {"name": "sales_followup", "version": "v1"}
    assert data["latency_ms"] > 0
    assert data["usage"]["cost_usd"] > 0


def test_chat_with_template_version_client(client: TestClient) -> None:
    response = client.post(
        "/v1/chat",
        json=template_payload(template_version="v1"),
    )

    assert response.status_code == 200
    assert response.json()["data"]["prompt_template"]["version"] == "v1"


def test_chat_without_template_has_null_fingerprint(client: TestClient) -> None:
    response = client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "hello"}], "subject_id": "user-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["prompt_template"] is None


def test_template_missing_variable_returns_validation_error(client: TestClient) -> None:
    response = client.post(
        "/v1/chat",
        json=template_payload(template_vars={"profile": "只有资料"}),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_1001"
    assert "history" in str(response.json()["error"])


def test_unknown_template_returns_validation_error(client: TestClient) -> None:
    response = client.post("/v1/chat", json=template_payload(template_name="no_such_template"))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_1001"


def test_stream_with_template_includes_latency(client: TestClient) -> None:
    with client.stream("POST", "/v1/chat/stream", json=template_payload()) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"latency_ms"' in body
    assert '"prompt_template"' in body
