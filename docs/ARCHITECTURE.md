# Architecture

## Layers

```text
HTTP / CLI / worker
        │
        ▼
API routes (validation, auth placeholder, response envelope)
        │
        ▼
Domain services / business errors
        │
        ├──────────────► Risk engine
        │                   │
        ▼                   ▼
LLM gateway ──────────► Provider adapter ──────────► Third-party API
        │
        ▼
Secrets resolver / audit / metrics / logs
```

Rules:

- Business code never imports a third-party LLM SDK directly.
- Business code never reads environment variables or secret files directly.
- API routes do not contain business rules; they validate input and translate
  domain results.
- Infrastructure failures are translated into `AppError` subclasses at the
  boundary.

## Request lifecycle

1. `RequestContextMiddleware` generates or accepts `X-Request-ID`, binds
   structured log context and starts the timer.
2. `SecurityHeadersMiddleware` adds browser security headers.
3. FastAPI validates the request with Pydantic (`extra="forbid"`).
4. The route builds an `LLMRequest` and a `RiskSubject`.
5. `LLMGateway` evaluates risk rules, checks the circuit breaker, calls the
   provider with a timeout and retry policy, calculates cost and writes audit
   fields.
6. The response is returned as `{"ok": true, "data": ...}`. Expected failures
   are returned as `{"ok": false, "error": ...}` with a stable error code and
   `request_id`.

## Error handling

```text
AppError
├── ValidationError
├── AuthenticationError
├── PermissionDeniedError
├── NotFoundError
├── ConflictError
├── BusinessRuleError
├── RateLimitError
├── ExternalServiceError
├── UpstreamTimeoutError
├── ConfigurationError
└── InternalError
```

Domain errors (`app/domain/errors.py`) subclass the relevant core error. Error
codes and HTTP status mappings live in `app/core/error_codes.py`.

Unhandled exceptions always become `INFRA_5001`; stack traces, SQL, provider
response bodies and secret values are never returned to clients.

## LLM call flow

```text
LLMGateway.complete / stream
  ├── _prepare            # system prompt, max_tokens clamp, optional PII masking
  ├── RiskEngine.evaluate # kill switch, allowlist, length, rate limit, quota,
  │                       # budget, content safety, prompt injection
  ├── CircuitBreaker      # per provider
  ├── Provider call       # timeout + bounded retries with backoff
  ├── CostCalculator      # input/output tokens → USD
  ├── RiskEngine.record_usage
  └── AuditLogger + metrics
```

Provider adapters:

- `AnthropicProvider`: official `anthropic` SDK, default `claude-opus-5-5`,
  adaptive thinking, streaming, refusal handling.
- `OpenAICompatProvider`: `POST /chat/completions` over `httpx`.
- `FakeLLMProvider`: deterministic local/test provider.

## Secret resolution

```text
config YAML: secret://llm/anthropic_api_key
                      │
                      ▼
             SecretResolver (TTL cache)
                      │
                      ▼
      Env / File / Vault / AWS Secrets Manager
                      │
                      ▼
                SecretValue
                      │
              reveal() only at SDK boundary
```

`SecretValue.__repr__` and `__str__` always return `***`. The logging redaction
processor also masks any `SecretValue` and any sensitive key.

## Environment model

| Environment | Purpose | Default LLM | Secrets | Risk |
|---|---|---|---|---|
| local | developer machine | fake | env | permissive |
| test | unit/integration/e2e | fake | env | disabled |
| staging | pre-production verification | real | file/vault | enforced |
| prod | production traffic | real | file/vault | enforced, fail-closed |
