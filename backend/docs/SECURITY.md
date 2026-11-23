# Security notes

## Secrets

- Configuration files contain only `secret://` references.
- `SecretValue` never exposes plaintext through `repr`, `str` or logging.
- Staging/prod fail fast when a required secret is missing.
- Use separate credentials per environment and per provider.
- Prefer short-lived credentials and OIDC over long-lived CI secrets.
- `.env` is local-only and gitignored. `.env.example` contains placeholders.

## Logging

- Sensitive keys are masked recursively:
  `password`, `secret`, `token`, `api_key`, `authorization`, `cookie`,
  `private_key`, `credential`, `signature`, `session_id`.
- `input_tokens`, `output_tokens`, `max_tokens` are explicitly allowlisted so
  cost telemetry is not masked.
- LLM prompt and response bodies are not logged. The gateway logs model,
  token counts, cost, duration and status.
- Audit events go to the `audit` logger and should be routed to a separate,
  append-only sink with a longer retention period.

## Third-party LLM calls

- All calls go through `LLMGateway`; adapters are confined to
  `src/app/llm/providers/`.
- Risk rules run before the provider is called: kill switch, model allowlist,
  input length, rate limits, quotas, budget, content policy and prompt
  injection heuristics.
- PII masking can be enabled with `llm.pii_masking_enabled`; extend the
  patterns for your domain.
- Provider error bodies are not returned to clients or logged.
- Refusals are surfaced as `LLM_3002`, not as successful responses.

## Production checklist

- [ ] `APP_ENV=prod`
- [ ] `server.docs_enabled=false`
- [ ] `logging.json=true`
- [ ] `cors_origins` contains real origins, never `*`
- [ ] `secrets.provider` is `file`, `vault` or `aws`
- [ ] `secrets.required` lists every mandatory secret
- [ ] `risk.fail_closed=true`
- [ ] `llm.allowed_models` lists only approved models
- [ ] `llm.daily_budget_usd` is set
- [ ] Redis is enabled for shared rate limits and quotas
- [ ] `/metrics` is reachable only from the monitoring network
- [ ] CI runs `bandit`, `pip-audit` and `gitleaks`
- [ ] Container runs as non-root with a read-only root filesystem
- [ ] Access logs and audit logs have retention and alerting
