# 私域销售跟进 Agent

给一线销售用的企微私域助手：读客户资料与最近互动，判断所处阶段，指出最值得推进的一件事，给一条能直接发出去的微信话术。

**当前状态（诚实版）**：这是一个「AI 应用工程底座 + 一个提示词模板」的形态，不是完整 Agent——还没有工具层和循环。按 `../sales-followup-agent-capability-plan.md` 的四阶段路线逐步长出来，每加一块必须同时交出一个可失败用例和一个数字。

## 两个目录，分别部署

| 目录 | 内容 | 部署方式 |
|---|---|---|
| `backend/` | FastAPI 服务：多环境配置、统一错误信封、风控（限流/配额/预算/熔断/幂等）、LLM 网关、密钥隔离、四段式提示词模板、健康检查与 Prometheus 指标 | 独立镜像，暴露 8000 |
| `frontend/` | 静态页面 + nginx（无 npm 构建）：流式对话、模板变量、每次调用的耗时/token/费用、调用详情 JSON | 独立镜像，暴露 80，反代 `/v1` 与 `/health` 到后端 |
| `deploy/` | compose（本地一键起）与 k8s 清单（前后端各一套 Deployment/Service） | — |

前后端是两个镜像、两个容器，**任一端可以单独回滚**，互不牵连。

## 端口

| 服务 | 宿主机 | 容器内 | 说明 |
|---|---|---|---|
| 前端 | 3000 | 80 | 浏览器访问入口 |
| 后端 | 8080 | 8000 | 对外映射到 8080，避开宿主机 8000 上的其它服务 |
| Redis | 6379 | 6379 | 可选用（`redis.enabled`） |

## 快速开始

```bash
cd deploy
docker compose up --build
# 前端 http://localhost:3000
# 后端 http://localhost:8080/health/ready
```

后端默认 `LLM_PROVIDER=fake`，所以不配任何密钥也能完整跑通「提问 → 模板渲染 → 流式回答 → 耗时/费用」这条链路，适合演示与截图。

想接真模型：在 `backend/config/local.yaml` 里把 `llm.provider` 换成 `openai_compat`（或 `anthropic`），密钥用 `secret://` 引用挂载，**不要写进镜像环境变量**。

## 改代码前先看这两个文件

- `backend/README.md`：后端能力清单、API 表、四段式模板的用法与工程约束
- `frontend/README.md`：页面在演示什么、如何加字段（为什么两边必须同时改）

## 目录

```
.
├── backend/            # FastAPI 服务（详见 backend/README.md）
│   ├── src/app/        # 配置 / 错误 / 风控 / LLM 网关 / 提示词模板 / 可观测
│   ├── tests/          # unit / integration / e2e / contract
│   ├── config/         # base + local/test/staging/prod
│   ├── docs/           # ARCHITECTURE / RUNBOOK / SECURITY / 设计要点
│   └── Dockerfile
├── frontend/           # 静态前端 + nginx（详见 frontend/README.md）
│   ├── index.html
│   ├── assets/         # app.js（无依赖 ES Module）、styles.css
│   ├── templates/      # nginx 模板，upstream 由 BACKEND_HOST 注入
│   └── Dockerfile
├── deploy/
│   ├── docker-compose.yml
│   └── k8s/            # 后端 deployment/service + 前端 deployment/service
└── .github/workflows/  # CI 按 backend/ 与 frontend/ 分别跑；CD 分别构建两个镜像
```

## 这套工程里值得看的三处

1. **统一错误信封**：`{"ok": false, "error": {code, message, details, retryable, request_id}}`，业务异常、参数校验、风控拒绝、5xx 全部收敛到同一形状，前端只需要一种解析逻辑。
2. **四段式提示词模板**（`backend/src/app/llm/prompts/templates.py`）：角色/任务/约束/输出格式，构造时缺段即报错；按 `name + version` 注册，指纹写进响应与审计，事后能还原「这条回答是哪版提示词产出的」。
3. **就绪与存活分开**：`/health/live` 只证明进程活着，`/health/ready` 才代表密钥、风控、LLM 网关都初始化完成；优雅停机时先置 `ready=False` 再关资源，避免流量打到半死的实例。

## 已知边界

- 没有工具层与循环，客户状态还不能现查，写操作还不能真的落库——这是接下来第二、三阶段的事；
- `frontend` 没有登录，`subject_id` 由前端固定传 `web-console`；接真实鉴权时必须由后端从令牌取，不能信前端传值；
- k8s 清单是可用的骨架（含前端 `readOnlyRootFilesystem` 所需的三处 emptyDir），但没在真实集群验证过。
