# Kubernetes deployment notes

1. Create the config map from the versioned config files:

```bash
kubectl create configmap sales-followup-agent-config \
  --from-file=base.yaml=config/base.yaml \
  --from-file=prod.yaml=config/prod.yaml
```

2. Create the secret. The key is mounted as `llm/anthropic_api_key`, matching
   `secret://llm/anthropic_api_key` in `config/prod.yaml`:

```bash
kubectl create secret generic sales-followup-agent-secrets \
  --from-file=anthropic_api_key=./secrets/anthropic_api_key
```

3. Apply the manifests:

```bash
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
```

For production, prefer an External Secrets Operator or CSI Secret Store instead
of manually created Secrets. Do not put secret values in ConfigMaps or image
environment variables.
