"""量启动期索引成本：同一份语料，冷启动 / 热启动 / 只读三种情况各花多久。

这不是评测脚本，是「把债修掉前后那个数字」的复现方式：
    .venv/bin/python scripts/measure_startup.py --embedder real
"""

from __future__ import annotations

import argparse
import time

from eval_common import build_embedder, describe

from app.core.config import load_config
from app.domains import load_domain
from app.retrieval.factory import build_vector_store
from app.tools.bootstrap import bootstrap_domain


def run(label: str, settings, pack, embedder, *, mode: str) -> dict[str, float]:
    store = build_vector_store(
        settings.retrieval.model_copy(update={"indexing": mode}), dim=settings.embedding.dim
    )
    started = time.perf_counter()
    boot = bootstrap_domain(
        pack,
        ":memory:",
        embedder,
        min_score=settings.retrieval.min_score,
        vector_store=store,
        indexing=mode,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    stats = boot.retriever.last_stats
    if hasattr(store, "close"):
        store.close()  # type: ignore[union-attr]
    print(
        f"{label:<24} {elapsed_ms:8.1f} ms | 算向量 {stats.embedded if stats else 0} 次 | "
        f"库里 {boot.retriever.size} 条"
    )
    return {"ms": elapsed_ms, "embedded": float(stats.embedded if stats else 0)}


def main() -> int:
    parser = argparse.ArgumentParser(description="启动期索引成本")
    parser.add_argument("--embedder", choices=("config", "hash"), default="config")
    args = parser.parse_args()

    settings = load_config()
    pack = load_domain(settings.app.domain)
    embedder = build_embedder("hash" if args.embedder == "hash" else "real")
    backend, table = settings.retrieval.backend, settings.retrieval.table
    print(f"backend={backend} table={table} embedder={describe(embedder)}")
    run("冷启动 sync（库是空的）", settings, pack, embedder, mode="sync")
    run("热启动 sync（库已建好）", settings, pack, embedder, mode="sync")
    run("只读启动 readonly", settings, pack, embedder, mode="readonly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
