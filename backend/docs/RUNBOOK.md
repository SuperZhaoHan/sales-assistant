# Runbook

## Local development

```bash
make setup
make check-config
make run
```

The local environment uses `FakeLLMProvider`; no third-party calls are made.

## Configuration checks

```bash
# Show the resolved configuration (secret references only, no values)
.venv/bin/python -m app.cli check-config

# Verify required secrets for the active environment
APP_ENV=staging .venv/bin/python -m app.cli check-secrets
```

## Secret provisioning

### Environment (local/CI only)

```bash
export LLM_ANTHROPIC_API_KEY=sk-ant-...
```

### File (Docker/Kubernetes)

```bash
mkdir -p /run/secrets/llm
printf '%s' "$LLM_ANTHROPIC_API_KEY" > /run/secrets/llm/anthropic_api_key
chmod 600 /run/secrets/llm/anthropic_api_key
```

The file provider rejects absolute paths and `..` traversal.

### Vault

```bash
pip install -e '.[vault]'
export APP__SECRETS__PROVIDER=vault
export APP__SECRETS__VAULT_URL=https://vault.example.com
export VAULT_TOKEN=...
```

Expected KV v2 layout: path `llm`, field `anthropic_api_key`.

### AWS Secrets Manager

```bash
pip install -e '.[aws]'
export APP__SECRETS__PROVIDER=aws
export APP__SECRETS__AWS_REGION=ap-southeast-1
```

Use `secret-name#field` for JSON secrets.

## Secret rotation

1. Create the new key in the provider console.
2. Write it to the secret backend under the same logical name.
3. Restart or call the resolver refresh endpoint (if you expose one).
4. Verify `check-secrets` and a staging chat request.
5. Revoke the old key.
6. Check audit logs for unexpected usage during the rotation window.

Never put a key in a ConfigMap, image layer, command-line argument or chat
message.

## Deployment

```bash
# Staging: push a tag or run the workflow manually
git tag v0.1.0
git push origin v0.1.0

# Production: run the protected workflow with an explicit image tag
```

Production deploys should be canary or rolling. The workflow contains
placeholders for `kubectl`/Helm; replace them with your platform commands.

## Rollback

```bash
kubectl rollout undo deployment/sales-followup-agent
```

Database migrations, if added later, must be backward compatible for one
release so rollback remains safe.

## LLM provider outage

1. Confirm the circuit breaker is open in `/metrics`
   (`circuit_breaker_state`).
2. Disable the provider with the kill switch (`risk.kill_switch=true`) if the
   service should stop calling it.
3. Reduce rate limits or quotas if the provider is degrading.
4. Check audit logs for error types and status codes.
5. If a fallback provider is configured, route traffic to it and verify cost
   accounting.

## Budget exhaustion

When `budget_exceeded` appears:

1. Inspect `llm_cost_usd_total` by provider/model.
2. Check the top tenants from audit logs.
3. Raise the budget only after identifying the traffic pattern.
4. Consider a cheaper approved model or lower max tokens; do not silently
   downgrade production quality.

## Key leak response

1. Revoke the key immediately at the provider.
2. Rotate the secret in the backend.
3. Search logs and CI artifacts for the key prefix.
4. Review provider usage for the exposure window.
5. Record the incident and update the runbook.
