from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.core.errors import ConfigurationError
from app.main import create_app


def test_staging_fails_fast_when_required_secret_missing() -> None:
    settings = load_config("staging").model_copy(deep=True)
    settings.secrets.provider = "env"
    settings.secrets.required = ["missing/secret"]
    settings.redis.enabled = False
    # 启动期会开库；这个用例只关心密钥校验，别在仓库里留下 data/app.db
    settings.app.store_path = ":memory:"
    settings.llm.provider = "fake"
    settings.llm.allowed_models = ["fake-model"]
    settings.observability.otel_enabled = False
    app = create_app(settings)

    with pytest.raises(ConfigurationError), TestClient(app):
        pass
