# 2.2 持久化：Postgres checkpointer + agent_threads 派生索引

- 提交：`0ea01e4`
- 日期：2026-11-28
- 结论：**状态归 checkpointer，索引归 agent_threads；索引是派生数据，丢了可重建。**

## 1. 为什么是两套东西

| 数据 | 存哪 | 谁写 | 丢了会怎样 |
| --- | --- | --- | --- |
| 图的状态（每个 super-step 快照、待写队列） | 官方三表 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` | LangGraph `AsyncPostgresSaver` | 任务丢失，不可恢复 |
| 线程索引（客户名 / 状态 / 最后节点 / 过期时间） | 薄表 `agent_threads`，**6 列，无 state 列** | 我们自己，三处幂等 upsert | 列表页看不到，但任务本身没事；跑一次脚本重建 |

禁止把 state 复制进业务表：那是双写，双写迟早一天两边对不上，且没人知道信谁。
索引里连 `state` 这一列都没有——代码层面没有抄的入口（`ThreadIndex.remember` 只收业务字段）。

## 2. 实测数字

| 指标 | 值 |
| --- | --- |
| 内存 saver：暂停 + 恢复一轮 | **5.7 ms** |
| 真库：跨重启恢复（含开连接池 + `setup()` + 恢复执行） | **22.4 ms** |
| 一个「建任务」线程暂停时落下的行 | `checkpoints` **6** / `checkpoint_blobs` **5** / `checkpoint_writes` **18** |
| 一个纯问答线程（4 节点）的 checkpoint 步数 | **6** |
| 真库全量测试（含 4 条 2.2 + 4 条 1.10b 真库用例） | **244 passed / 1 skipped，2.26 s** |
| 无库全量测试（真库用例自动 skip） | 236 passed / 9 skipped，1.99 s |

## 3. 可失败用例（`tests/integration/test_checkpoint_postgres.py`，4 条）

1. `test_state_survives_restart`：暂停 → **关掉连接池**（等价进程退出）→ 新 saver/新图实例 → `resume(True)` 续跑。断言：`stage=committed`、`commit` 只调 1 次、轨迹是 6 个节点**接上去的**（前 4 个没重跑）。
2. `test_index_upsert_is_idempotent`：同一线程写 5 次 → 只有 1 行。
3. `test_index_has_no_state_column`：`information_schema` 查出 6 列，断言 `state` 不存在。
4. `test_rebuild_restores_index_from_checkpoints`：清空索引 → `rebuild_threads` → 两条线程回来；暂停那条能认出 `awaiting_confirm` / `await_confirm`。

## 4. 三个必须说清的边界

- **异步**：FastAPI 是协程，图用 `ainvoke`，同步 `PostgresSaver` 在异步路径直接抛 `NotImplementedError`（没实现 `aget_tuple`）。所以用 `AsyncPostgresSaver` + `AsyncConnectionPool`。
- **autocommit**：`setup()` 里有 `CREATE INDEX CONCURRENTLY`，不能跑在事务块里；连接池必须 `kwargs={"autocommit": True}`，否则启动报 `ActiveSqlTransaction`。
- **暂停的语义**：`interrupt` 挂起时状态里 `stage` 仍是 `draft`（节点没返回）。「在等人」这个事实只能从 `pending_writes` 里的 `__interrupt__` 读出来——`rebuild_threads` 就是这么判的。索引里写 `awaiting_confirm` 必须由调用方显式传。

## 5. 复现命令（在 `backend/` 目录）

```bash
docker run -d --name sales-pg -e POSTGRES_PASSWORD=postgres -p 5482:5432 pgvector/pgvector:pg16

PG_TEST_DSN=postgresql://postgres:postgres@localhost:5482/postgres \
  .venv/bin/python -m pytest tests/integration/test_checkpoint_postgres.py -q

.venv/bin/python scripts/measure_checkpoint.py        # 上面那张数字表

AGENT_CHECKPOINT_DSN=postgresql://postgres:postgres@localhost:5482/postgres \
  .venv/bin/python scripts/rebuild_threads.py          # 索引丢了跑这个
```

## 6. 还没接的（2.3 起）

- DSN 还没进 `AppConfig`（现在用 `AGENT_CHECKPOINT_DSN` 环境变量），接 API 时一起收进去；
- 没有保留策略：checkpoint 会一直长，按天数或线程数清理是后续运维项；
- 索引的三处写入还没挂到 API 层（新建 / 停在 await_confirm / confirm 完成），2.3 接图到接口时补。
