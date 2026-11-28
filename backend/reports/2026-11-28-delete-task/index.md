# 2.7 撤销任务：逻辑删除，走同一条确认链路

日期：2026-11-28　结论：**暂停 5.0 ms → 确认 1.2 ms → 重放 0.0 ms；行还在（`status=deleted` + `deleted_at`），未删 0 / 含已删 1 / 审计 1**

## 1. 为什么要第二个写工具

阶段 2 的汇总口留了一个漏判：**「把张伟那条待办删了」**。它也是写操作，但词表里没有删除词，被判成闲聊。
把「删」加进词表能立刻满分，但那只是为指标改指标——真正的答案是把「写」这件事交给工具属性判定，而不是靠词表。

所以这一节做 `delete_task`，用它验证三件事：

1. 同一套确认链路（暂停 → 确认 → 执行 → 幂等重放）能不能直接复用；
2. 「写操作」能不能由 `is_write=True` 决定，而不是每个意图都补一个 if；
3. 删除必须是**逻辑删除**，能追溯、能恢复、能算报表。

## 2. 删除为什么是逻辑删除（核心口径）

物理 `DELETE` 之后，三件事全做不了：

- **审计**：回答不了「这条待办是谁、什么时候撤的」——2.5 的 `actions` 只记人际动作，业务事实在 `tasks`；
- **报表**：算不出「本月建了多少、撤了多少」，撤销率是销售管理最常用的一个指标；
- **恢复**：误删就永久没了，只能靠备份回滚整库。

所以只改两列：`status='deleted'` + `deleted_at`。行一直留着，列表默认过滤掉它，统计要 `include_deleted=True` 才看得见。

代价也要说清：表会一直长，需要保留期（比如 1 年后归档）；而且任何新写的查询都必须记得过滤 `status != 'deleted'`，漏一处就会把撤销的任务当有效任务算——这是软删除最常见的坑。

## 3. 实现（四处）

**① 存储层**（`store/sqlite_store.py`）

```python
UPDATE tasks SET status = 'deleted', deleted_at = ?
WHERE id = ? AND status != 'deleted'
```

- `AND status != 'deleted'` 是这个 UPDATE 自带的幂等开关：第二次进来影响 0 行，返回 `None`，调用方据此知道「已经撤过了」。
- `close_task()` 返回 `Task | None`；`find_open_task(customer)` 只挑未删的最近一条；`list_tasks/count_tasks` 默认排除已删。
- 老库补列走 `_ensure_task_columns`（`PRAGMA table_info` + `ALTER TABLE ADD COLUMN`），跑几次都幂等。

**② 工具层**（`tools/delete_task.py`）

- `DeleteTaskArgs(task_id, dedupe_key)`，`extra="forbid"`；`is_write=True`、`idempotent=True`——注册表会拦没声明幂等的写工具。
- handler 分三种结果：删了 → 「任务已撤销」；已是删除状态 → 「未做改动」；任务不存在 → `ValidationError`。
  第三种必须报错，否则「删了个不存在的任务」会被当成成功，人以为撤销成功了。

**③ 图**（`agent/graph.py`）

- 新增节点 `resolve_target`：删除指令只说了「删了」，没说删哪条，先用 `find_task(customer)` 认出目标；没有可删的 → `BusinessRuleError`（`code=NO_OPEN_TASK`），**不弹确认框**——框里写不出「删哪条」，点了也没意义。
- `route_after_classify` 多一条 `target` 分支；`route_after_draft` 的判据从「是不是 create」改成「意图集合里有没有写动作」。
- `commit_task_node` 按意图分派：create 走 `deps.commit_task`，delete 走 `deps.delete_task`，共用同一个 `await_confirm` 停点。

**④ 意图词表**（`domains/insurance/intents.yaml`）

`delete_task` **排在 `create_task` 前面**，因为「不用提醒我了」里含「提醒我」——顺序错了就会被当成「再建一个」。这是词表式意图识别的典型坑，模块 docstring 里已经写过一次。

## 4. 数字

`scripts/measure_delete.py`（内存 saver + 内存库）：

| 步骤 | 耗时 | 结果 |
| --- | --- | --- |
| 暂停（等人点确认） | **5.0 ms** | `__interrupt__=True`，任务仍 open |
| 确认后执行 | **1.2 ms** | `confirmed`，删掉的是 `tk-54a4ea…` |
| 重放一次（2.5 的幂等） | **0.0 ms** | `confirmed`，不重复写，审计仍 1 行 |
| 落库证据 | — | 行还在且 `status=deleted`、`deleted_at=2026-11-28T09:01:57+00:00`；未删 0 / 含已删 1 |

识别集（`scripts/measure_write_detection.py`）扩到 **21 条**（写 11 / 只读 6 / 闲聊 4）：

- 正确率 **20/21 = 95.2%**，非写误弹 **0/10**；
- 新增两条：`把张伟那条待办删了` 现在**判对了**（走 `resolve_target` → 确认），`张伟这单先关了吧` 仍漏判（口头「关单」不在词表）——保留这一条是故意的，用来说明规则的天花板。

测试：`tests/unit/test_delete_task.py` **8 条 / 0.06 s**；无库全量 **271 passed / 9 skipped**（1.77 s），真库 **279 passed / 1 skipped**（2.02 s）。

## 5. 可失败用例

1. 把逻辑删除改回 `DELETE FROM tasks` → 「含已删 = 1」断言红，而且再也没法恢复、没法算撤销率。
2. 去掉 `AND status != 'deleted'` → 第二次调用影响 1 行、返回任务而不是 `None`，幂等断言红，`deleted_at` 会被反复改写。
3. 去掉确认直接执行（pending 时就删）→ `test_confirm_gates_delete[False]` 红：拒绝之后任务必须是 `open`。
4. `find_open_task` 不过滤 `status` → 已删任务还能被选成目标，`test_find_open_task_skips_deleted` 红。
5. 词表顺序把 `create_task` 放到 `delete_task` 前 → 「不用提醒我了」变 `create_task`，`test_classify_routes_delete_before_create` 红。

## 6. 边界（继续诚实）

- **删除对象靠"最近一条"猜**：`find_open_task` 取该客户最新未删任务；客户名下有多条时可能猜错。真产品得让确认框列出候选（或让模型从上下文带 `task_id`），这里先不装。
- **撤销后能不能恢复**：行还在，技术上一条 `UPDATE` 就能恢复，但**没有恢复接口**。
- **没有保留期清理**：`deleted` 行会一直长（和 2.2 的 checkpoint 一个性质），归档策略仍未做。
- **「写由工具属性决定」还差半步**：图里仍是"意图 → 写不写"的判断，`is_write` 决定的是确认与登记门槛。真正由工具计划驱动确认，要等阶段 4 把工具 MCP 化、规划器直接产出工具调用时才成立。
