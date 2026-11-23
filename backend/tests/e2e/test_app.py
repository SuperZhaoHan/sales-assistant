from __future__ import annotations

from fastapi.testclient import TestClient


def test_full_chat_flow(client: TestClient) -> None:
    health = client.get("/health/live")
    chat = client.post(
        "/v1/chat",
        json={
            "messages": [{"role": "user", "content": "hello e2e"}],
            "subject_id": "e2e-user",
        },
    )

    assert health.status_code == 200
    assert chat.status_code == 200
    assert chat.json()["data"]["text"] == "[fake:fake-model] hello e2e"


def test_docs_and_metrics_are_disabled_in_test(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/metrics").status_code == 404


def test_demo_error_route_exists_in_test(client: TestClient) -> None:
    response = client.get("/v1/demo/business-error")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BIZ_2001"
