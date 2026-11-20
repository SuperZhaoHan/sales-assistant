from __future__ import annotations

from fastapi.testclient import TestClient


def test_business_error_envelope(client: TestClient) -> None:
    response = client.get("/v1/demo/business-error")

    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "BIZ_2001"
    assert body["error"]["message"] == "余额不足"
    assert body["error"]["details"] == {"required": 100, "available": 10}
    assert body["error"]["request_id"]


def test_validation_error_envelope(client: TestClient) -> None:
    response = client.get("/v1/demo/validation-error")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_1001"


def test_risk_error_envelope(client: TestClient) -> None:
    response = client.get("/v1/demo/risk-error")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RISK_4002"


def test_internal_error_hides_details(client: TestClient) -> None:
    response = client.get("/v1/demo/internal-error")

    assert response.status_code == 500
    body = response.text
    assert '"code":"INFRA_5001"' in body
    assert "intentional demo failure" not in body
    assert "Traceback" not in body


def test_request_validation_error(client: TestClient) -> None:
    response = client.post("/v1/chat", json={})

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_1001"
    assert body["error"]["details"]["fields"]


def test_not_found(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND_1001"


def test_error_request_id_matches_header(client: TestClient) -> None:
    response = client.get(
        "/v1/demo/business-error",
        headers={"X-Request-ID": "error-request-id"},
    )

    assert response.headers["X-Request-ID"] == "error-request-id"
    assert response.json()["error"]["request_id"] == "error-request-id"
