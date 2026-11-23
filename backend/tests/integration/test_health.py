from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import AppConfig
from app.main import create_app


def test_live(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["env"] == "test"


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "test-request-id"})

    assert response.headers["X-Request-ID"] == "test-request-id"


def test_security_headers(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_metrics_endpoint_when_enabled(test_settings: AppConfig) -> None:
    settings = test_settings.model_copy(deep=True)
    settings.observability.metrics_enabled = True
    app = create_app(settings)

    with TestClient(app) as client:
        client.get("/health/live")
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text
