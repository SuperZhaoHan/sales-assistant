from __future__ import annotations

from fastapi.testclient import TestClient


def chat_payload(content: str = "hello") -> dict[str, object]:
    return {
        "messages": [{"role": "user", "content": content}],
        "subject_id": "user-1",
        "tenant_id": "tenant-1",
    }


def test_chat_with_fake_provider(client: TestClient) -> None:
    response = client.post("/v1/chat", json=chat_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["provider"] == "fake"
    assert body["data"]["model"] == "fake-model"
    assert body["data"]["text"] == "[fake:fake-model] hello"
    assert body["data"]["usage"]["input_tokens"] > 0
    assert body["data"]["usage"]["output_tokens"] > 0
    assert body["data"]["usage"]["cost_usd"] > 0


def test_chat_stream(client: TestClient) -> None:
    with client.stream("POST", "/v1/chat/stream", json=chat_payload()) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"delta"' in body
    assert '"done": true' in body
    assert "[DONE]" in body


def test_chat_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post(
        "/v1/chat",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "unexpected": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_1001"
