# 前端（frontend）

私域销售跟进 Agent 的 Web 控制台。纯静态页面 + nginx，**没有 npm 构建步骤**。

## 目录

```
frontend/
├── index.html        # 页面骨架：顶栏状态、左侧参数、中间对话、右侧调用详情
├── assets/
│   ├── app.js        # 全部逻辑（ES Module，无依赖）
│   └── styles.css
├── nginx.conf        # 静态托管 + /v1、/health 反代（SSE 已关缓冲）
├── Dockerfile        # nginx:1.27-alpine，三层 COPY
└── README.md
```

## 这个页面在演示什么

不是「做个聊天框」，而是把后端的几个能力变成看得见的东西：

| 界面上看到的 | 对应后端能力 |
|---|---|
| 右上角「后端就绪 · local · 0.1.0」 | `/health/ready`（就绪探针，未就绪返回 503） |
| 回答按「阶段判断 / 建议动作 / 话术」三段 | 四段式提示词模板 `sales_followup/v1` 的输出格式段 |
| 回答下方「首字 320 ms · 总耗时 1540 ms · $0.000123」 | `/v1/chat/stream` 的 done 事件（`latency_ms` + `usage`） |
| 「模板 sales_followupv1」小标签 | 响应里的 `prompt_template` 指纹，用于回溯「哪版提示词产出的」 |
| 发送中按钮变「停止」 | 客户端取消（配合 nginx `proxy_ignore_client_abort off`） |
| 右侧「调用详情」里的原始 JSON | 统一错误信封 `/v1/chat` 的 `{ ok, error: { code, request_id } }` |

## 本地怎么跑

推荐直接用 compose（前端、后端一起起，同源、无 CORS 问题）：

```bash
cd ../deploy
docker compose up --build
# 前端 http://localhost:3000   后端 http://localhost:8080/docs（local 环境默认关闭，见下）
```

单独调试前端时，把 `assets/app.js` 开头的 `API_BASE` 改成 `http://localhost:8080/v1`，
并在后端 `config/local.yaml` 里把 `server.cors_origins` 配成 `["http://localhost:3000"]`——
否则浏览器会拦跨域请求。**这不影响生产，生产走的是同源反代。**

## 怎么改

- 换配色/文案：只动 `styles.css` 和 `index.html`，`app.js` 里没有硬编码样式。
- 加一个输入项：`index.html` 加控件 → `buildPayload()` 里加字段 → 后端 `ChatRequest` 加同名字段。
  后端是 `extra="forbid"`，多传一个没登记过的字段会直接 422，所以两边必须同时改——这是刻意的。
- 加一个指标：`buildMetricList()` 里加一项，数据从 done 事件里取，取不到就退化成 `—`，不要编。

## 为什么不引入 React

三个理由，面试可以直接说：

1. 这个项目的重点是 Agent 引擎（工具层、确认、幂等、轨迹），不是前端框架；
2. 不引入构建链，镜像里就没有 node 和 node_modules，部署只需一个 nginx；
3. 前端只消费两个接口契约，将来真要换成 React，接口层不用动，换的是 `app.js` 这一层。

## 已知边界

- **upstream 在 nginx 启动时解析**：`proxy_pass` 里是静态主机名，所以后端 Service / 容器必须先存在（compose 有 `depends_on`，k8s 要先 apply 后端的 Service）。如果后端 DNS 还没就绪，容器会 `host not found in upstream` 启动失败，靠重启恢复——这是 nginx 的固有行为，不是配置错误。
- 会话状态全在内存（刷新即清空），没有做多标签页同步；
- 没有登录，`subject_id` 固定写 `web-console`；接真实鉴权时应由后端从令牌里取，**不能信前端传的值**；
- 没有做流式 Markdown 渲染，模板输出的是固定三段纯文本，用 `pre-wrap` 足够。
