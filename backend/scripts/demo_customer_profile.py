"""1.2 演示：真实查一次客户档案，并把数字打出来。

跑法：``.venv/bin/python scripts/demo_customer_profile.py``
数据落在一个临时文件里，跑完就删，不会污染 backend/data/。
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from app.core.errors import ValidationError
from app.domains import load_domain
from app.tools.bootstrap import bootstrap_domain
from app.tools.registry import execute, reset_registry

SEED = Path(__file__).resolve().parents[1] / "src/app/domains/insurance/seed"


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "demo.db"

        started = time.perf_counter()
        reset_registry()
        boot = bootstrap_domain(load_domain("insurance"), path)
        build_ms = (time.perf_counter() - started) * 1000
        store, names = boot.store, boot.tools

        print(f"建库 + 灌种子：{build_ms:.1f} ms，联系人 {store.contact_count()} 行")
        print(f"注册表：{names}，语料 {boot.retriever.size} 条")
        print(f"库文件：{path.stat().st_size / 1024:.0f} KB\n")

        print("— 有资产 —")
        print(await execute("customer_profile", {"customer_name": "张伟"}), "\n")

        print("— 无资产 —")
        print(await execute("customer_profile", {"customer_name": "王芳"}), "\n")

        print("— 查不到 —")
        print(await execute("customer_profile", {"customer_name": "张三"}), "\n")

        print("— 模型多塞一个字段 —")
        try:
            await execute("customer_profile", {"customer_name": "张伟", "tenant_id": "t-999"})
        except ValidationError as exc:
            print(f"{exc} | errors={exc.details['errors']}\n")

        # 单次查询耗时：含 asyncio.to_thread 的线程切换
        rounds = 200
        started = time.perf_counter()
        for _ in range(rounds):
            await execute("customer_profile", {"customer_name": "张伟"})
        per_call_us = (time.perf_counter() - started) / rounds * 1e6
        print(f"单次查询（含线程切换）：{per_call_us:.0f} 微秒（{rounds} 次平均）")

        store.close()


if __name__ == "__main__":
    asyncio.run(main())
