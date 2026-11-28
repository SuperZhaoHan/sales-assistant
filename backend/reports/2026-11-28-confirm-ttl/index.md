# 2.4 超时默认拒绝：确认窗口过了就 409，一行任务都不写

日期：2026-11-28　｜　分支 main　｜　真库全量 259 passed / 1 skipped 1.93 s（无库 251 / 9）

## 这一节解决什么

2.3 只保证了「确认前不写」。但「确认」这一步本身有个现实问题：**人可能走开了**。
半小时后回来顺手点一下确认，比「不确认」更危险——他大概率已经不记得上下文。

所以规则是：**确认窗口（默认 300 秒）内点才有效，超过就默认拒绝**，并留一条审计。

## 改动

| 文件 | 改动 |
| --- | --- |
| `src/app/core/config.py` + `config/base.yaml` | 新增 `agent.confirm_ttl_seconds=300`、`agent.checkpoint_dsn`（空 = 内存 saver） |
| `src/app/agent/confirm.py` | `confirm_task`：先判有无待确认 → 再判 TTL → 最后才 `resume`；超时写 `actions.status=expired`、**照样 `resume(False)` 让线程走到 aborted 终态**（不收尾会永远挂着）、返回 409 |
| `src/app/store/sqlite_store.py` | 新增 `actions` 表（审计留痕）+ `record_action/list_actions/count_actions` |
| `src/app/agent/runtime.py` | 把图 + checkpointer + 业务库装成 `AgentRuntime`；`checkpoint_dsn` 空时退回内存 saver 并在启动日志告警 |
| `src/app/api/v1/tasks.py` | `POST /v1/tasks/confirm`：服务层结论 → HTTP（200 / 409 `ConflictError`） |
| `src/app/main.py` | 启动期装配 runtime、`/v1` 挂载 tasks 路由、关机时关连接池 |
| `tests/unit/test_confirm.py` | 6 条：窗口内 / 超时 / 边界 / 拒绝 / 未知线程 / 重复确认 |
| `tests/integration/test_confirm_api.py` | 2 条：未知线程 409 信封、多塞字段被拒 |

## 数字（`scripts/measure_confirm.py`）

| 指标 | 结果 |
| --- | --- |
| TTL 内确认 | **2.6 ms**，`status=confirmed`，`tasks` +1 |
| 超时确认（默认拒绝） | **1.5 ms**（含 resume 收尾），`status=expired`，`code=CONFIRM_EXPIRED`，该线程 **0 行**，interrupt 清空、终态 `aborted` |
| 审计 | 两条（一条 confirmed、一条 expired） |
| 单测 | 6 条 **0.06 s**；接口 2 条 **0.06 s** |
| 全量（真 PG） | **259 passed / 1 skipped 1.93 s**；无库 **251 / 9** |

## 可失败用例

1. **把 TTL 判定挪到 `ainvoke` 之后** → `test_expired_confirm_is_rejected_and_audited` 红：副作用（1 行任务）已经发生，409 只是事后通知。
2. **`>` 改成 `>=`** → `test_exactly_at_ttl_is_still_allowed` 红：恰好 300 秒被误判超时。
3. **删掉 `record_action(expired)`** → 审计断言红：拒绝没有留痕，事后无法解释「为什么没建任务」。
3b. **超时分支只 `return 409`、不 `resume(False)`** → 收尾断言红：`snapshot.interrupts` 非空、`stage` 仍是 `awaiting_confirm`，线程变成永久僵尸（列表页一直「待确认」）。
4. **重复确认**：第二次 confirm 必须 `missing`（没有待确认项），`tasks` 仍 1 —— 这条同时是 2.5 的引子。

## 开发中踩到的坑（值得记住）

第一版把「暂停时刻」写进 state（`confirm_requested_at`），结果 TTL 永远是「刚刚暂停」。
原因：`interrupt` 暂停时节点**还没返回**，写在节点里的字段要等恢复才有值。
最终用 checkpoint 的 `snapshot.created_at` —— 它就是「线程停下的那一刻」，恢复时不会变。

## 边界（没做的）

- **没有「发起确认」接口**：待确认线程目前只能在图/脚本里产生；真实链路是「对话 → 草稿 → 等人点确认」，前端与起草接口还没接。
- **默认内存 saver**：`agent.checkpoint_dsn` 为空时重启即丢线程；真库要显式配。
- **终态收尾只做到「图走到 aborted」**：`agent_threads` 投影与 checkpoint 归档（到点删除）没做，过期线程仍占存储、列表页需要靠 rebuild/重建才不被误认。
- **并发两次 confirm 没有分布式锁**：第二次会落到 `missing`（checkpointer 串行），但两个进程同时恢复同一线程的行为没测过。
- **`actions` 只是审计表**：幂等结果复用、重放返回原结果在 2.5。

## 复现

```bash
cd ~/project/sales-followup-agent/backend
.venv/bin/python -m pytest tests/unit/test_confirm.py tests/integration/test_confirm_api.py -q
.venv/bin/python scripts/measure_confirm.py
```
