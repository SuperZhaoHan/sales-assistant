# 2.5 幂等重放：同一个确认点 3 次只写 1 行，第 2/3 次回放原结果

日期：2026-11-28　范围：`confirm_task` 一条写路径　结论：**3 次重放 → `tasks` 1 行、决策 1 行、任务号三次相同，重放 0.2 ms（不碰图）**

## 1. 为什么之前的 `confirm` 不幂等

`record_action` 的 id 是 `sha256(thread_id|kind|status|now)[:12]`，把时间戳也算进去，天然每次都不一样，
所以每次确认都新插一行审计；而第二次确认又因为 `snapshot.interrupts` 已经空了，被判 `missing` 回 409。

用户真正的诉求是「我手抖点了两下 / 网络重试了一次，后端别写两条任务，也别告诉我没这回事」。
所以判重点不是"有没有人在写"，而是**"这个请求是不是同一件事"**。

## 2. 幂等键怎么定义

```python
decision_key(thread_id, approved) = sha256(f"{thread_id}|confirm_task|{'yes' if approved else 'no'}")
```

- **线程**：同一个客户、同一次对话里的确认，才有资格算"同一件事"；不带 thread_id 就分不出是哪个客户被确认了。
- **动作名**：`confirm_task`，为以后加写工具（改客户、发消息）留位置，不会跨动作串味。
- **决定本身**：`approved` 必须进 key。先点确认、再点拒绝是两个不同请求；如果把它们并成一个 key，
  重放会把"拒绝"回放成"确认成功"——这是最危险的一类串味，比"多写一行"严重得多。
- **不放时间戳**：时间戳进 key 就等于"永远不幂等"。

完整 64 位 sha256 存库，`act-` 前缀取前 12 位只是给人看的 id（和 2.3 的 `tk-` 同一套做法）。

## 3. 存储：加两列 + 部分唯一索引 + `INSERT OR IGNORE`

```sql
ALTER TABLE actions ADD COLUMN idem_key TEXT;
ALTER TABLE actions ADD COLUMN result   TEXT;   -- {"status":"confirmed","task_id":"tk-..."}
CREATE UNIQUE INDEX uq_actions_idem ON actions(idem_key) WHERE idem_key IS NOT NULL;
```

- 老库靠 `_ensure_action_columns` 补列（`CREATE TABLE IF NOT EXISTS` 只对新库生效），只加列、不改列，跑几次都幂等。
- 唯一索引是**部分索引**（`WHERE idem_key IS NOT NULL`）：原来 `record_action` 写的审计行 `idem_key` 为空，不受约束，历史行不需要迁移。
- 写入 `INSERT OR IGNORE`：判定权交给数据库。先 `get` 再 `insert` 是非原子的——两个 confirm 同时到，
  两边都查不到、都插，就是两行；唯一索引 + `INSERT OR IGNORE` 才保证"最终只落一次"。

## 4. 流程：先回放，再动作，最后落结果

```
get_decision(key) 有值  → 直接返回原结果（不读图、不写业务表）
       ↓ 无
aget_state 判有无 interrupt → 判 TTL → resume → 拿到 stage/task_id
       ↓
save_decision(key, status, result)   ← 在 resume 成功之后才写
```

三个刻意选择：

1. **先查决策再读图**：重放命中时不碰 checkpointer，实测 0.2 ms（正常确认 2.8–4.4 ms）。
2. **结果在动作之后落库**：先记"成功"再 resume，一旦 resume 抛错，重放就会永远拿到一个没发生过的成功。
3. **超时也留决策**：`expired` 同样存一条（`{"status":"expired","age_seconds":...}`），所以超时后用户再点
   拿到的是 409 `CONFIRM_EXPIRED`（"你这次超时了"），而不是 409 `NO_PENDING_CONFIRM`（"像没这回事"）。

## 5. 数字

`scripts/measure_confirm.py`（内存 saver + 内存库）：

| 步骤 | 耗时 | 结果 |
| --- | --- | --- |
| TTL 内确认 | 4.4 ms | `confirmed`，`tasks=1` |
| 超时确认（默认拒绝） | 2.4 ms | `expired` / `CONFIRM_EXPIRED` |
| **重放 3 次** | **0.2 ms（3 次合计）** | `confirmed`，同一个任务号 `True`，`tasks=1` |
| 收尾 | — | `tasks=1`，审计 2 行（1 条确认 + 1 条超时，都是决策行） |

测试：真库全量 **262 passed / 1 skipped**（1.87 s）；无库 **254 passed / 9 skipped**（1.59 s）。
`test_confirm.py` 9 条用例 0.10 s，其中新增 3 条：重放 3 次同号、超时重放仍 expired、反向决定不是重放。

## 6. 可失败用例（每条都能独立把红线打回来）

1. **删掉 `get_decision` 前置查询** → 第 2 次确认变 `missing` 409，`test_second_confirm_replays_first_result` 红。
2. **key 里去掉 `approved`** → 确认后点拒绝被回放成"确认成功"，`test_opposite_decision_is_a_different_request` 红。
3. **把 `INSERT OR IGNORE` 换成 `INSERT OR REPLACE`（或普通 INSERT + 先查后写）** → 后一次覆盖前一次，
   "原结果"变"最新结果"，重放任务号可能变；并发下还可能写两行（唯一索引报错或两行并存）。
4. **先 `save_decision` 再 `resume`** → resume 失败时留下"已确认"的假记录，重放永远不再执行，任务永远丢失。
5. **超时分支不存决策** → 用户再点得到 `NO_PENDING_CONFIRM`，一线客服会理解成"系统丢了这笔"。

## 7. 边界与欠账（面试会追）

- **并发两个 confirm 同 key**：两边都可能通过 `get_decision` 的空检查，最终两行不会写（唯一索引 + `OR IGNORE` 兜住），
  但两次 `resume` 会依次跑，第二次撞空 interrupt 返回 `missing`——目前靠"先落库的那次赢"，没有分布式锁。
- **决策表没有 TTL 清理**：审计按合规留 1–3 年，`expires_at` 不是删除触发器（见 2.2 讨论）。
- **只覆盖 `confirm` 一个动作**：`tasks` 的写入幂等靠 2.3 的 `dedupe_key`，两条路径各自独立，没做统一网关。
- **`risk/idempotency.py` 的 `IdempotencyStore` 协议没用上**：它是"短窗口去重"的内存/Redis 实现，
  而这里要的是"结果回放"，权威必须在数据库；协议留着给未来的非幂等第三方调用用。
- **API 层没有加 `Idempotency-Key` 头**：目前幂等键由服务端从 `(thread_id, approved)` 派生，
  客户端无需配合；如果以后允许"同一线程同一决定但不同载荷"，得改成客户端传 key。
