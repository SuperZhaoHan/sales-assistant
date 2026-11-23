from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "prod"]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"
ENV_OVERRIDE_PREFIX = "APP__"
LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class RuntimeSettings(BaseSettings):
    """Values needed before the layered YAML configuration is loaded."""

    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    env: Environment = "local"
    config_dir: Path = DEFAULT_CONFIG_DIR


class AppMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "sales-followup-agent"
    version: str = "0.1.0"


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    workers: int = Field(default=1, ge=1)
    cors_origins: list[str] = Field(default_factory=list)
    docs_enabled: bool = False
    request_id_header: str = "X-Request-ID"


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    json_format: bool = True
    access_log: bool = True
    redact_keys: list[str] = Field(default_factory=list)

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in LOG_LEVELS:
            raise ValueError(f"logging.level must be one of {sorted(LOG_LEVELS)}")
        return normalized


class SecretsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["env", "file", "vault", "aws"] = "env"
    file_dir: str = "/run/secrets"
    required: list[str] = Field(default_factory=list)
    cache_ttl_seconds: int = Field(default=300, ge=0)
    env_prefix: str = ""
    vault_url: str | None = None
    vault_mount: str = "secret"
    vault_token_env: str = "VAULT_TOKEN"
    aws_region: str | None = None


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["fake", "anthropic", "openai_compat"] = "fake"
    model: str = "fake-model"
    base_url: str | None = None
    api_key_ref: str | None = "secret://llm/anthropic_api_key"
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0)
    circuit_failure_threshold: int = Field(default=5, ge=1)
    circuit_recovery_seconds: float = Field(default=30.0, gt=0)
    max_tokens: int = Field(default=4096, gt=0)
    max_output_tokens: int = Field(default=8192, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    thinking: Literal["adaptive", "disabled"] | None = "adaptive"
    allowed_models: list[str] = Field(default_factory=list)
    system_prompt: str | None = None
    max_input_chars: int = Field(default=20000, gt=0)
    cost_per_1m_input: float = Field(default=4.0, ge=0.0)
    cost_per_1m_output: float = Field(default=20.0, ge=0.0)
    daily_budget_usd: float | None = Field(default=None, ge=0.0)
    pii_masking_enabled: bool = False
    stream: bool = False


class RateLimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    requests: int = Field(default=60, gt=0)
    window_seconds: int = Field(default=60, gt=0)


class QuotaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    daily_requests: int = Field(default=1000, gt=0)
    daily_tokens: int = Field(default=1_000_000, gt=0)


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    fail_closed: bool = True
    kill_switch: bool = False
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)
    max_input_chars: int = Field(default=20000, gt=0)
    blocked_terms: list[str] = Field(default_factory=list)
    prompt_injection_enabled: bool = True
    model_allowlist_enabled: bool = True
    budget_enabled: bool = True


class RedisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    url: str = "redis://localhost:6379/0"


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_endpoint: str | None = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env: Environment = "local"
    app: AppMeta = Field(default_factory=AppMeta)
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        if self.env in {"staging", "prod"}:
            if self.server.docs_enabled:
                raise ValueError("server.docs_enabled must be false outside local/test")
            if not self.logging.json_format:
                raise ValueError("logging.json_format must be true outside local/test")
            if not self.llm.allowed_models:
                raise ValueError("llm.allowed_models must not be empty outside local/test")
            if not self.secrets.required:
                raise ValueError("secrets.required must not be empty outside local/test")

        if self.env == "prod":
            if self.llm.provider == "fake":
                raise ValueError("prod must not use the fake LLM provider")
            if self.risk.enabled and not self.risk.fail_closed:
                raise ValueError("prod risk control must fail closed")
            if "*" in self.server.cors_origins:
                raise ValueError("prod cors_origins must not contain '*'")
            if self.secrets.provider == "env":
                raise ValueError("prod must not use env as the secret provider")

        return self


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"configuration file must contain a mapping: {path}")
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_env_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _deep_set(target: dict[str, Any], path: list[str], value: Any) -> None:
    cursor = target
    for part in path[:-1]:
        node = cursor.setdefault(part, {})
        if not isinstance(node, dict):
            raise ValueError(f"cannot apply environment override under {'.'.join(path)}")
        cursor = node
    cursor[path[-1]] = value


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    result = _deep_merge({}, config)
    for key, raw in os.environ.items():
        if not key.startswith(ENV_OVERRIDE_PREFIX):
            continue
        path = [part.lower() for part in key[len(ENV_OVERRIDE_PREFIX) :].split("__") if part]
        if not path:
            continue
        _deep_set(result, path, _parse_env_value(raw))
    return result


def load_config(
    env: Environment | None = None,
    config_dir: Path | None = None,
) -> AppConfig:
    """Load base.yaml, {env}.yaml and APP__* environment overrides.

    Secret values never come from YAML. The config only stores secret references
    such as ``secret://llm/anthropic_api_key``.
    """

    requested_env = env or os.getenv("APP_ENV", "local")
    if requested_env == "local":
        load_dotenv(PROJECT_ROOT / ".env", override=False)

    runtime = RuntimeSettings()
    resolved_env: Environment = env or runtime.env
    resolved_dir = Path(config_dir) if config_dir is not None else runtime.config_dir

    merged = _deep_merge(
        _read_yaml(resolved_dir / "base.yaml"),
        _read_yaml(resolved_dir / f"{resolved_env}.yaml"),
    )
    merged["env"] = resolved_env
    merged = _apply_env_overrides(merged)
    return AppConfig.model_validate(merged)
