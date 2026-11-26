"""1.4 演示：改库后再问，答案跟着变；以及裁剪到底裁掉了什么。

跑法：``.venv/bin/python scripts/demo_truth_source.py``
"""

from __future__ import annotations

from app.agent.brief import MAX_ASSETS_IN_PROMPT, build_brief, split_assets
from app.domains import load_domain
from app.store.sqlite_store import Asset
from app.tools.bootstrap import open_store


def main() -> None:
    pack = load_domain("insurance")
    store = open_store(":memory:", pack.root / "seed")

    print("— 第一次查询（库里的原值）—")
    before = build_brief(store, "张伟")
    print(before.history)
    print(f"facts={before.facts}\n")

    store.record_interaction("c-1001", "2026-11-25")
    print("  ↑ 已把「最近互动」改成 2026-11-25，没重启、没清缓存\n")

    print("— 第二次查询（同一个进程、同一个 store）—")
    after = build_brief(store, "张伟")
    print(after.history)
    print(f"facts={after.facts}")
    print(f"旧值还在吗：{'2026-11-18' in after.facts}\n")

    print(f"— 裁剪：8 条资产只放 {MAX_ASSETS_IN_PROMPT} 条进提示词 —")
    assets = tuple(
        Asset(
            id=f"a-{i:03d}",
            contact_id="c-1001",
            product=f"测试产品{i}",
            status="已失效" if i == 8 else "待续保",
            expires_at=f"2026-{i:02d}-01",
            premium_cents=100_000,
        )
        for i in range(1, 9)
    )
    shown, omitted = split_assets(assets)
    print(f"展示 {len(shown)} 条，裁掉 {omitted} 条")
    print("展示的到期日：", [a.expires_at for a in shown])
    print("被裁掉的是第 8 条（已失效），其余按到期日先后排序")

    store.close()


if __name__ == "__main__":
    main()
