# 2.3 写操作加确认：确认前 0 行，确认后 1 行，重放 3 次还是 1 行

日期：2026-11-28　｜　分支 main　｜　本地全量 243 passed / 9 skipped 2.07 s（真库齐全另算）

## 这一节解决什么

只读工具跑错最多读到旧数据；写工具跑错会在业务系统里留下真任务。
所以「建跟进任务」必须同时满足三条：

1. 人在图里点过确认才写（`interrupt` → `resume(True)`）；
2. 同一件事重复提交只落一行（幂等键 + 派生 id + UNIQUE）；
3. 键由运行时算，不由模型编（模型编的 key 每次都不同，重放就变两条）。

## 改动

| 文件 | 改动 |
| --- | --- |
| `src/app/store/sqlite_store.py` | 新增 `tasks` 表（`dedupe_key` UNIQUE）+ `Task` dataclass + `add_task/get_task/list_tasks/count_tasks`；`add_task` 用 `INSERT OR IGNORE`，id = `tk-` + sha256(key) 前 12 位 |
| `src/app/tools/create_task.py` | 新工具 `create_task`：`is_write=True`、`idempotent=True`、`extra="forbid"`；参数含 `dedupe_key` 但描述写明由运行时传入 |
| `src/app/agent/tasks.py` | `dedupe_key(thread_id, tool, args)`：参数按 key 排序后紧凑 JSON，再 sha256；`make_committer(store)` 把图的提交接到 sqlite |
| `src/app/agent/graph.py` | `commit_task_node` 读 `config["configurable"]["thread_id"]` 并带入提交载荷（thread_id 不进 state） |
| `src/app/tools/bootstrap.py` | 工具清单加 `create_task`（第三个） |
| `tests/unit/test_create_task.py` | 7 条用例，0.40 s |

## 数字（`scripts/measure_write.py`）

| 指标 | 结果 |
| --- | --- |
| 跑到 interrupt（暂停） | **4.9 ms**，`tasks=0` |
| `resume(True)` 确认一次 | **1.8 ms**，`tasks=1` |
| 同一载荷重放 3 次 | **0.5 ms**，`tasks` 仍 **1** |
| 任务号 | `tk-e936caaa477e`（同一 key 三次同一个号） |
| 本地全量 | **243 passed / 9 skipped 2.07 s**（未设 `PG_TEST_DSN`） |

## 4 条可失败用例（每条都能把改动打红）

1. **确认前不写**：把 `commit_task_node` 的 `confirmed` 判断去掉、改成无条件写 → `test_no_task_before_confirm` 红（暂停时就落库）。
2. **重放只一行**：把 `INSERT OR IGNORE` 换成普通 `INSERT` → 第二次重放触发 `dedupe_key` UNIQUE 冲突（派生 id 相同还会主键冲突），`test_replay_three_times_keeps_one_row` 红。说明幂等是两层：派生 id + 唯一约束。
3. **键里必须有 thread_id**：`make_committer` 里把 `thread_id` 从 key 里去掉 → `test_same_content_different_thread_creates_two_rows` 红（两个会话被合并成一条）。
4. **参数顺序不能影响键**：`dedupe_key` 去掉 `dict(sorted(...))` → `test_dedupe_key_ignores_argument_order` 红（同样的字典换个插入顺序算出两个键）。

## 边界（这一节没做，别在面试里说做了）

- **HTTP 接口还没接**：`POST /api/v1/tasks/confirm` 属于 2.4（TTL + 409 语义一起做），当前只有图和存储两层。
- **`actions` 表与 `IdempotencyStore` 没接**：2.5 才把幂等结果落表，现在幂等靠存储层唯一约束。
- **任务标题固定模板**（`为{customer}确认续保`），不是模型生成；`due_at` 目前恒为 `None`，自然语言时间解析未做。
- **业务库仍在 SQLite**（`data/app.db`），未搬 PG；多租户闸门未触发。

## 复现

```bash
cd ~/project/sales-followup-agent/backend
.venv/bin/python -m pytest tests/unit/test_create_task.py -q
.venv/bin/python scripts/measure_write.py
```
