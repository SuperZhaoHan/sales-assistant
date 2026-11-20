from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import AppConfig, load_config
from app.main import create_app


@pytest.fixture(scope="session")
def test_settings() -> AppConfig:
    return load_config("test")


@pytest.fixture
def app(test_settings: AppConfig):
    return create_app(test_settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by directory so ``pytest -m unit`` and CI stages stay simple."""

    for item in items:
        path = str(item.fspath)
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)
        elif "/tests/contract/" in path:
            item.add_marker(pytest.mark.contract)
