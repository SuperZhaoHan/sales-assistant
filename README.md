# 私域销售跟进 Agent（sales-followup-agent）

> 环境要求：Python 3.15（Makefile 使用 `python3.15`；可自行调整）。项目声明的最低版本见 `pyproject.toml`。

面向生产环境的 Python 服务基座，包含：

- 多环境配置：`local` / `test` / `staging`（预发）/ `prod`（线上）
- 结构化日志、请求 ID、访问日志、审计日志、Prometheus 指标
- 统一业务异常、错误码、全局异常处理与标准错误响应
- 分层的独立测试体系：unit / integration / e2e / contract
- 风控模块：限流、配额、预算、熔断、幂等、内容安全、提示词注入检测、Kill Switch
- 第三方大模型统一网关：超时、重试、熔断、成本核算、审计、脱敏
- 密钥隔离：`SecretProvider` + `SecretResolver`，配置只保存 `secret://` 引用
- CI/CD：GitHub Actions、pre-commit、Docker、compose、可选 K8s
- 可选依赖：Anthropic SDK、Redis、Vault、AWS Secrets Manager、OpenTelemetry

## 快速开始

```bash
# 1. 创建/复用虚拟环境并安装开发依赖
make setup

# 2. 校验配置（默认 local）
.venv/bin/python -m app.cli check-config

# 3. 启动服务
make run
# 或
.venv/bin/python -m uvicorn app.asgi:app --host 0.0.0.0 --port 8000 --reload
```

国内网络安装依赖较慢时，可临时指定镜像（不会写入项目配置）：

```bash
.venv/bin/python -m pip install -e ".[dev]" \
  --index-url https://mirrors.aliyun.com/pypi/simple/
```

安装为 wheel 或在容器中运行时，需要把配置目录显式传给服务：

```bash
export APP_CONFIG_DIR=/app/config
```

打开：

- 健康检查：<http://localhost:8000/health/live>
- 就绪检查：<http://localhost:8000/health/ready>
- API 文档（仅 local/test）：<http://localhost:8000/docs>
- 指标：<http://localhost:8000/metrics>

本地默认使用 `FakeLLMProvider`，不会调用任何第三方接口。

## 环境与配置

环境由 `APP_ENV` 控制：`local`、`test`、`staging`、`prod`。

加载顺序（后者覆盖前者）：

1. `config/base.yaml`
2. `config/{APP_ENV}.yaml`
3. 环境变量 `APP__SECTION__KEY`，例如：

```bash
export APP__LOGGING__LEVEL=DEBUG
export APP__LLM__PROVIDER=fake
export APP__RISK__RATE_LIMIT__REQUESTS=100
```

配置使用 Pydantic 严格校验，未知字段会直接启动失败。

### 环境差异

| 项 | local | test | staging | prod |
|---|---|---|---|---|
| 日志 | 控制台 | 控制台 | JSON | JSON |
| API 文档 | 开启 | 关闭 | 关闭 | 关闭 |
| LLM | fake | fake | 真实 | 真实 |
| 风控 | 宽松 | 关闭 | 强校验 | 强校验 |
| 密钥 | env | env | file/vault | file/vault |
| Redis | 可选 | 关闭 | 开启 | 开启 |

## 密钥与私钥隔离

配置中只允许出现密钥引用：

```yaml
llm:
  api_key_ref: secret://llm/anthropic_api_key
```

密钥通过 `SecretProvider` 解析：

- `env`：`llm/anthropic_api_key` → `LLM_ANTHROPIC_API_KEY`
- `file`：从 `/run/secrets` 或 Docker/K8s 挂载目录读取
- `vault`：HashiCorp Vault KV v2（`pip install -e '.[vault]'`）
- `aws`：AWS Secrets Manager（`pip install -e '.[aws]'`）

约束：

- `SecretValue` 的 `repr`/`str` 永远输出 `***`，只有显式 `reveal()` 才能取值。
- 密钥值不会进入日志、错误详情、指标或 trace。
- staging/prod 启动时会校验 `secrets.required`，缺失则失败。
- `.env` 仅用于本地，已被 `.gitignore` 忽略。
- CI 建议使用 OIDC 获取短期云凭证，避免长期密钥落入仓库。

## 错误体系

所有预期异常继承 `AppError`：

```python
from app.domain.errors import InsufficientBalanceError

raise InsufficientBalanceError(details={"required": 100, "available": 10})
```

统一错误响应：

```json
{
  "ok": false,
  "error": {
    "code": "BIZ_2001",
    "message": "余额不足",
    "details": {"required": 100, "available": 10},
    "retryable": false,
    "request_id": "..."
  }
}
```

错误码定义在 `src/app/core/error_codes.py`，业务错误码可通过 `register_error_spec` 扩展。

## LLM 网关

业务代码只能通过 `LLMGateway` 调用大模型：

```python
from app.llm.base import LLMMessage, LLMRequest
from app.risk.models import RiskSubject

response = await gateway.complete(
    LLMRequest(
        messages=(LLMMessage(role="user", content="你好"),),
        model="claude-opus-5-5",
    ),
    RiskSubject(subject_id="user-1", tenant_id="tenant-1"),
)
```

网关负责：

- 风控检查（限流、配额、预算、模型白名单、内容安全、提示词注入）
- 超时、指数退避重试、熔断
- token 与成本核算
- 审计日志（不记录 prompt/response 正文）
- 统一异常映射

Provider：

- `fake`：本地/测试
- `anthropic`：官方 SDK，默认 `claude-opus-5-5`，支持 adaptive thinking 和流式输出
- `openai_compat`：任意 OpenAI Chat Completions 兼容接口

启用 Anthropic：

```bash
pip install -e '.[anthropic]'
export APP__LLM__PROVIDER=anthropic
export APP__LLM__MODEL=claude-opus-5-5
export LLM_ANTHROPIC_API_KEY=sk-ant-...
```

## 提示词模板（四段式）

提示词不进业务代码，统一放在 `src/app/llm/prompts/`，按 `name + version` 存取。所有模板固定四段，缺一不可：

| 段落 | 决定什么 | 为什么必须有 |
|---|---|---|
| 【角色】 | 模型站在谁的立场说话 | 同一个问题，站销售和站客户两种立场答案相反 |
| 【任务】 | 让它做什么 | 不写清会跑偏成「写一篇分析」而不是「给下一步动作」 |
| 【约束】 | 它不能做什么 | 编造客户信息、乱承诺折扣、越权执行写操作，都在这段挡住 |
| 【输出格式】 | 结果长什么样 | 下游要能解析，销售要能直接复制粘贴 |

当前内置一版：`sales_followup/v1`（企微私域销售跟进：判断阶段 → 建议动作 → 可发送话术）。

代码里直接用：

```python
from app.llm.prompts import get_template

system = get_template("sales_followup").render(
    {
        "profile": "客户张总，制造业，已试用 5 天",
        "history": "上周问过价格，未报价",
    }
)
```

调用接口时也可以不自己拼提示词，把模板名和变量传进来，由后端渲染：

```bash
curl -s http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "下一步怎么跟？"}],
    "template_name": "sales_followup",
    "template_vars": {"profile": "客户张总，制造业，已试用 5 天", "history": "上周问过价格，未报价"}
  }'
```

响应里同时带回**耗时与费用**，以及这次用的是哪版模板：

```json
{
  "ok": true,
  "data": {
    "text": "...",
    "usage": {"input_tokens": 512, "output_tokens": 96, "total_tokens": 608, "cost_usd": 0.0024},
    "latency_ms": 812.35,
    "prompt_template": {"name": "sales_followup", "version": "v1"}
  }
}
```

三条工程约束：

- **版本化**：改提示词等于改产品行为，必须带版本号，可回滚、可对比 A/B；
- **变量缺失直接报错**（`VALIDATION_1001`），不渲染半成品提示词——半成品比报错更难排查；
- **模板身份进审计**：`prompt_template` 的 name/version 随 `llm_request` 落到审计日志，事后能还原“这条回答是哪版提示词产出的”。

## 风控

`RiskEngine` 按顺序执行规则，任一规则拒绝即返回：

- Kill Switch
- 模型白名单
- 输入长度
- 限流（内存 / Redis）
- 日配额（请求数 / token）
- 日预算（USD）
- 内容安全
- 提示词注入

local/test 默认使用内存实现；staging/prod 配置 `redis.enabled=true` 并使用共享计数。

## 测试

```bash
make test-unit
make test-integration
make test-e2e
make test-contract
make coverage
```

测试目录：

- `tests/unit`：纯内存，无网络
- `tests/integration`：应用、异常、密钥、风控集成
- `tests/e2e`：完整应用 + FakeLLM
- `tests/contract`：第三方 API 契约（respx 模拟）
- `tests/fakes`：FakeLLM 等替身
- `tests/factories`：测试数据工厂

测试依赖放在 `[project.optional-dependencies].dev`，不会进入生产包。

## CI/CD

GitHub Actions：

- `.github/workflows/ci.yml`：lint、mypy、unit/integration 矩阵、安全扫描、构建
- `.github/workflows/cd-staging.yml`：预发部署骨架
- `.github/workflows/cd-prod.yml`：生产部署骨架（需 Environment 审批）
- `.github/workflows/nightly.yml`：夜间 e2e / 依赖审计

本地提交前：

```bash
pre-commit install
pre-commit run --all-files
```

## Docker

```bash
docker build -f deploy/Dockerfile -t sales-followup-agent:local .
docker compose -f deploy/docker-compose.yml up --build
```

生产建议：

- 非 root 用户运行
- 只读根文件系统
- 密钥通过挂载或 External Secrets 注入，不使用镜像 ENV
- 配置健康检查、资源限制和 NetworkPolicy

## 安全基线

- 生产关闭 debug、API 文档和详细错误
- CORS 使用白名单，禁止 `*`
- 请求体大小、超时、输入长度限制
- 依赖锁定 + `pip-audit`、`bandit`、`gitleaks`
- 日志脱敏：password/token/api_key/authorization/private_key 等
- LLM prompt/response 默认不落日志
- 第三方 SDK 只能出现在 `src/app/llm/providers/`
- 密钥轮换与泄露应急流程见后续运维手册

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [运维手册](docs/RUNBOOK.md)
- [安全说明](docs/SECURITY.md)

## 命令行

```bash
.venv/bin/sales-followup-agent --help
.venv/bin/sales-followup-agent --env test check-config
.venv/bin/sales-followup-agent --env staging check-secrets
.venv/bin/sales-followup-agent serve --reload
```
