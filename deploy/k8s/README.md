# Kubernetes deployment notes

本目录是两个独立部署单元：`deployment.yaml` + `service.yaml` 是后端 API，
`frontend-deployment.yaml` + `frontend-service.yaml` 是前端（nginx，反代到后端 Service）。

1. 用版本化的配置文件创建 ConfigMap（命令在仓库根执行），后端的 `APP_CONFIG_DIR` 是 `/app/config`：

```bash
kubectl create configmap sales-followup-agent-config \
  --from-file=base.yaml=backend/config/base.yaml \
  --from-file=prod.yaml=backend/config/prod.yaml
```

2. 创建 Secret。键会挂载成 `llm/anthropic_api_key`，对应 `config/prod.yaml` 里的
   `secret://llm/anthropic_api_key`：

```bash
kubectl create secret generic sales-followup-agent-secrets \
  --from-file=anthropic_api_key=./secrets/anthropic_api_key
```

3. 应用清单：

```bash
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/frontend-deployment.yaml
kubectl apply -f deploy/k8s/frontend-service.yaml
```

4. 前端如何找到后端：前端的 nginx 配置在容器启动时由 `envsubst` 渲染，
   upstream 取自环境变量 `BACKEND_HOST` / `BACKEND_PORT`。这里填的是后端 **Service 名**
   `sales-followup-agent`，所以不需要 Ingress 或硬编码 IP。

5. 前端需要一个 Ingress（或 `kubectl port-forward svc/sales-followup-agent-web 3000:80`）
   才能从集群外访问；Ingress 的注解里记得关闭响应缓冲，否则流式回答会被攒成一整段：

```yaml
nginx.ingress.kubernetes.io/proxy-buffering: "off"
nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
```

生产环境建议用 External Secrets Operator 或 CSI Secret Store 替代手工 Secret，
不要把密钥值写进 ConfigMap 或镜像的环境变量里。
