# 启动期不再重建索引（1.10c）

日期：2026-11-27 | 分支 `main` | 前置：1.10 真 embedding、1.10b pgvector

## 一句话结论

**启动从「每次重算 30 条向量」变成「0 次」：热启动 1708.8 ms → 22.8 ms，只读 16.5 ms。**
代价是语料变更必须显式跑一次 `scripts/build_index.py`；只读部署忘记跑，**启动直接失败**，不会带病返回旧知识。

## 数字

同一份 30 条语料、同一把尺子（`BAAI/bge-m3` 1024 维）、同一个库（`sales-pg` / 表 `chunks`）：

| 场景 | 耗时 | 算向量次数 | 说明 |
| --- | --- | --- | --- |
| 改造前（每次启动） | 1708.8 ms | 30 | `bootstrap_domain → build_retriever → index()` |
| 冷启动 `sync`（库空） | 1170.2 ms | 30 | 该花的钱，只在首次部署/重建时出现 |
| 热启动 `sync`（库已建好） | **22.8 ms** | **0** | 语料没变，一次 embedding 都不花 |
| 只读启动 `readonly` | **16.5 ms** | **0** | 不写库，只对账 |

三条附带的数字：

- 建表语句不再每次执行：`_schema_ready()` 一次往返探测（表 / `content_sha` 列 / HNSW 索引），之前的 377.4 ms 里约 300 ms 是 5 条幂等 DDL。
- 删一条语料后重建：`新算 1 | 未变 29` —— 增量只补缺的那一条，不是全量 30 条。
- 删库一行后只读启动：**启动被拦下**，`details = {'missing': ['kb-030'], 'stale': [], 'extra': [], 'hint': '先跑 scripts/build_index.py ...'}`。

## 机制

1. **指纹判变更**：`content_sha = sha256(title + "。" + text)`，与 embedding 用的是同一段文字（`fingerprint.embed_text`）。不比 id——id 是引用锚点，正文改了没人改 id，只看 id 会把改动当成「已存在」跳过。
2. **`Retriever.sync(chunks)`**：新增 / 过期（指纹不同）才 `embed_many`，其余跳过；最后 `prune(keep_ids)` 清掉语料里已经没有的行。返回 `IndexStats(added, updated, unchanged, removed, total)`，`embedded = added + updated`。
3. **`Retriever.audit(chunks)`**：只读模式用。缺条 / 过期 / 多余三类都算不一致，抛 `ConfigurationError` 并给出重建命令。
4. **`rebuild=True`**：先 `prune(())` 清空再全量算——换模型、换维度时旧向量整体作废，靠它而不是靠人手删表。
5. **`retrieval.indexing: sync | readonly`**：默认 `sync`（本地/CI，无变更即 0 次）；`readonly` 给「库由外部 job 拥有」的部署。`build_vector_store` 在 `readonly` 下传 `prepare=False`，连库但不建表。
6. **存储侧**：`chunks` 表加 `content_sha text`；老表用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 补列（补完默认空串 → 首轮被判成「全过期」，自愈一次全量）。`VectorStore` 协议新增 `content_shas()` / `prune()`，内存与 pgvector 两实现同语义。

## 可失败用例

`tests/unit/test_index_sync.py`（8 条）：

| 用例 | 删掉哪段实现会红 |
| --- | --- |
| `test_second_sync_embeds_nothing` | 把有人改回 `index()` 全量 → 红 |
| `test_changed_text_reembeds_that_row` | 指纹比对改回「id 存在就跳过」→ 红（并校验检索结果里是新正文，不只是 sha 变了） |
| `test_removed_chunk_is_pruned` | 删掉 `prune` → 红 |
| `test_rebuild_recomputes_everything` | 删掉 `rebuild` 的清空 → 红 |
| `test_readonly_refuses_stale_index` | 删掉 `audit` 的 `missing` 判定 → 红 |
| `test_readonly_refuses_changed_text` | 删掉 `audit` 的 `stale` 判定 → 红 |
| `test_readonly_accepts_consistent_index` | 兼校验「对账不花一次 embedding」 |
| `test_first_sync_embeds_all` | 基线 |

`tests/integration/test_pgvector_store.py` 新增 `test_content_sha_matches_memory_and_prune`：两后端 `content_shas()` 必须完全相同，`prune` 两边都删 1 条（真库跑，需 `PG_TEST_DSN`）。

本地：`230 passed / 4 skipped`（不设 `PG_TEST_DSN`）；设了之后 pgvector 4 条全绿。

## 复现

```bash
cd backend
.venv/bin/python scripts/build_index.py --rebuild        # 冷路径：30 次 embedding
.venv/bin/python scripts/build_index.py                  # 热路径：0 次
.venv/bin/python scripts/measure_startup.py              # 冷/热/只读 三态
PG_TEST_DSN=postgresql://postgres:postgres@localhost:5482/postgres \
  .venv/bin/python -m pytest tests/integration/test_pgvector_store.py -q
```

## 边界与没做的事

- **两个实例同时启动**：`sync` 幂等（`ON CONFLICT id DO UPDATE`），但会重复算同一批向量，多花钱不脏数据。真要并发安全应上 advisory lock，本轮没做。
- **只读模式不校验维度**：表里若是 256 维旧数据、查询是 1024 维，会在第一条查询报错而不是启动报错。写明了，没加探测。
- **蓝绿切表**：`retrieval.table` + `build_index.py --table chunks_v2` 已有口子，但「切配置 + 保留旧表回滚」没做成脚本。
- **多租户**：`prune` 用的是全局 id 集合；将来按租户分库/分表时，`keep_ids` 必须按租户切，否则 A 租户的同步会删掉 B 租户的行。
