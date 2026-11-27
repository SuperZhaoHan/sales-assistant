"""离线建索引：把「重建向量」从启动路径里挪出去，只在语料变更时跑一次。

为什么要有这个脚本：
- app 启动时只需要「读」，不该顺手把 30 条语料重算一遍向量（真模型 1.7 s + 30 次 HTTP）；
- 建库是「写」，要有幂等、要能重建、要能蓝绿切表——这些都不适合塞进 lifespan；
- 分开之后，启动要么 0 次 embedding（sync 且无变更），要么直接失败（readonly 且不一致）。

跑法（在 backend/ 目录）：
    .venv/bin/python scripts/build_index.py                    # 增量补齐：无变更就是 0 次
    .venv/bin/python scripts/build_index.py --rebuild           # 全量重算：换模型/换维度后必跑
    .venv/bin/python scripts/build_index.py --table chunks_v2   # 蓝绿：新表建好，验完改配置再切
"""

from __future__ import annotations

import argparse
import time

from eval_common import build_embedder, describe

from app.core.config import load_config
from app.domains import load_domain
from app.retrieval.store import PgVectorStore
from app.tools.bootstrap import build_retriever


def main() -> int:
    parser = argparse.ArgumentParser(description="离线建索引（pgvector）")
    parser.add_argument("--embedder", choices=("config", "hash"), default="config")
    parser.add_argument("--rebuild", action="store_true", help="先清空再全量重算")
    parser.add_argument("--table", default=None, help="覆盖 retrieval.table（蓝绿切表用）")
    args = parser.parse_args()

    settings = load_config()
    if settings.retrieval.backend != "pgvector" or not settings.retrieval.dsn:
        raise SystemExit("retrieval.backend 不是 pgvector：内存索引随进程消失，没有离线建这回事")

    kind = "hash" if args.embedder == "hash" else "real"

    if kind == "hash":
        print("注意：强制哈希向量入库，与线上 bge-m3 的维度/分数不通用（仅供排查）")
    embedder = build_embedder(kind)
    pack = load_domain(settings.app.domain)
    table = args.table or settings.retrieval.table
    # 建库脚本当然要建表：prepare 保持默认 True
    store = PgVectorStore(settings.retrieval.dsn, dim=embedder.dim, table=table)
    print(f"目标表 {table} | embedder {describe(embedder)} | rebuild={args.rebuild}")

    started = time.perf_counter()
    try:
        retriever = build_retriever(
            pack.root / "corpus", embedder, store, indexing="sync", rebuild=args.rebuild
        )
        stats = retriever.last_stats
    finally:
        store.close()
    elapsed_ms = (time.perf_counter() - started) * 1000

    if stats is None:  # pragma: no cover - sync 模式必然有账
        raise SystemExit("没有拿到同步结果")
    print(
        f"新算 {stats.embedded} | 新增 {stats.added} | 过期重算 {stats.updated} | "
        f"未变 {stats.unchanged} | 删除 {stats.removed} | 库里共 {stats.total} 条"
    )
    print(f"耗时 {elapsed_ms:.1f} ms")
    if stats.embedded == 0:
        print("本次没有条目需要重算：库已经是语料的精确投影，可以直接用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
