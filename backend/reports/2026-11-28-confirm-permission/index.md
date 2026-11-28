# 2.6 确认动作的权限：viewer 能查不能确认，403 也要留痕

日期：2026-11-28　范围：`POST /v1/tasks/confirm` 的角色门槛　结论：**viewer / 无身份 / 未知角色一律 403，`tasks` 仍 0，审计多 1 行 `forbidden`；判定本身 2.08 µs**

## 1. 这一节解决什么

前面的确认链路（2.3–2.5）默认「谁能调这个接口谁就能确认」。真实私域团队里，坐席、主管、只读的运营实习生都在同一个系统里：
实习生点一下"确认"，系统就真的给客户建了待办。这不是权限设计问题，是**谁能代表业务做决定**的问题。

口径：`viewer` 可查不可确认；确认需要 `operator` 及以上；拿不到身份按最小权限（fail-closed）；未知角色直接拒，不降级。

## 2. 身份从哪来（先说清楚前提）

本服务**不做认证**，只做授权。身份由网关注入两个头：

```
X-Actor-Id: u-001
X-Actor-Role: operator
```

前提条件必须写进设计：**网关要剥掉客户端自带的同名头**。否则任何人加一行 `X-Actor-Role: admin` 就进去了——
这是"授权服务"和"认证服务"分工的边界，面试问到"你的鉴权在哪一层"就答这里。

## 3. 实现（三小段）

**① `src/app/security/roles.py`**（纯逻辑，不碰 HTTP，所以单测不用起服务）

```python
ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
CONFIRM_MIN_ROLE: Role = "operator"

def make_actor(actor_id, role) -> Actor:      # 规范化 + 未知角色拒绝
def ensure_confirm_permission(actor) -> None: # 不到 operator 就抛 PermissionDeniedError(ROLE_NOT_ALLOWED)
```

- 角色名 `strip().lower()` 规范化（`" Operator "` 能过），但**不认识的值直接 403 `ROLE_UNKNOWN`**。
  不降级成 viewer 的理由：降级看着安全，其实是把配置错误藏起来——运维以为配了 operator，系统默默按 viewer 跑，写操作全被拒却不报错。
- 无头时 `actor_id=anonymous, role=viewer`：拿不到身份等于最小权限。

**② `src/app/api/v1/actor.py`**　FastAPI 依赖，只负责取头、调 `make_actor`。

**③ `src/app/api/v1/tasks.py`**　在 handler 里判权限，不在服务层：

```python
try:
    ensure_confirm_permission(actor)
except PermissionDeniedError:
    store.record_action(thread_id=payload.thread_id, kind="confirm_task",
                        status="forbidden", detail=f"actor={actor.actor_id} role={actor.role}")
    raise
```

两个刻意的选择：

- **权限不进 `confirm_task`**：服务层只回答"能不能确认"，不认身份。同一份服务要被脚本、定时任务、未来的 MCP 工具调用，绑死 HTTP 角色会让它们全部走不通。
- **拒绝也要写审计**：只记"被拒了"不记"谁被拒"，出事查不出是谁在试探。审计行 `idem_key` 为空，不参与 2.5 的唯一索引。

## 4. 数字

`scripts/measure_permission.py`（1000 次平均）：

| 动作 | 耗时 |
| --- | --- |
| 被拒判定（含抛 `PermissionDeniedError`） | **2.08 µs / 次** |
| 放行判定 | **0.07 µs / 次** |
| 拒绝审计写入（SQLite 一行） | **0.27 ms / 行** |

一次 403 的真实成本几乎全在审计那一行，判定本身可以忽略——所以"拒绝也留痕"这个决定不受性能约束。

测试：真库全量 **271 passed / 1 skipped**（1.96 s）；无库 **263 passed / 9 skipped**（1.68 s）。
`test_roles.py` 6 条 + `test_confirm_api.py` 5 条，共 0.15 s。

## 5. 可失败用例

1. **无身份默认给 operator（fail-open）** → `test_missing_role_header_fails_closed` 红（匿名能确认）。
2. **未知角色降级成 viewer** → `test_unknown_role_is_rejected_not_dowgraded` 红（`superuser` 变成静默只读）。
3. **403 分支不写审计** → `test_viewer_cannot_confirm_and_is_audited` 里 `len(actions) == 1` 红。
4. **把 `CONFIRM_MIN_ROLE` 改成 viewer** → `test_viewer_is_denied` 红，线上等于没门槛。
5. **权限判定挪进 `confirm_task`** → 单测 `test_views...` 之外，所有用 `confirm_task(graph, store, ...)` 的脚本调用都得带 actor 参数；2.5 的 9 条用例直接改动，说明"服务层不该认识身份"。

## 6. 边界（诚实版）

- **只做授权不做认证**：没有 token 校验、没有签名，头被伪造的防线在网关。
- **扁平等级**，不是资源级 ACL：没有"这个坐席只能确认自己名下客户"这一层；那需要把 `customer_owner` 进判定，属于 3.x 的租户/配额范围。
- **「可查」这一半还没落地**：目前没有 `GET /v1/tasks` 列表接口，viewer 的读路径只有聊天和健康检查。等列表接口做出来，它的依赖应该是 `viewer` 门槛（可读），而不是复用 confirm 的。
- **审计表会随越权尝试增长**：目前每次 403 插一行（审计语义），没有做限流；如果被刷，需要按 `(actor, thread)` 限速而不是去重。
