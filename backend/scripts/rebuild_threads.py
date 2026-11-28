"""从 checkpoints 重建 agent_threads 索引。

为什么要有这个脚本：索引是派生数据，允许丢。丢了用这个脚本重建，
而不是去 checkpoint 里手抄状态——手抄就会产生第二份事实。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from psycopg import AsyncConnection

from app.agent.persistence import ThreadIndex, open_checkpointer, rebuild_threads

ENV_NAME = "AGENT_CHECKPOINT_DSN"


def _dsn(cli_value: str | None) -> str:
    value = cli_value or os.getenv(ENV_NAME, "")
    if not value:
        print(f"缺少 DSN：用 --dsn 传，或设环境变量 {ENV_NAME}", file=sys.stderr)
        raise SystemExit(2)
    return value


async def _run(dsn: str) -> int:
    saver, pool = await open_checkpointer(dsn)
    conn = await AsyncConnection.connect(dsn, autocommit=True)
    try:
        index = ThreadIndex(conn)
        await index.ensure_schema()
        rebuilt = await rebuild_threads(saver, index)
        total = await index.count()
        print(f"从 checkpoint 重建 {rebuilt} 条线程索引；agent_threads 现有 {total} 行")
        return 0
    finally:
        await conn.close()
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="重建 agent_threads 派生索引")
    parser.add_argument("--dsn", default=None, help=f"PG 连接串，默认读 {ENV_NAME}")
    args = parser.parse_args()
    return asyncio.run(_run(_dsn(args.dsn)))


if __name__ == "__main__":
    raise SystemExit(main())
