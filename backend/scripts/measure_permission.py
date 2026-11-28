"""2.6 计时：角色判定本身有多便宜，被拒一次要付多少审计成本。"""

from __future__ import annotations

import contextlib
import time

from app.core.errors import PermissionDeniedError
from app.security.roles import ensure_confirm_permission, make_actor
from app.store.sqlite_store import CustomerStore

ROUNDS = 1000


def main() -> None:
    viewer = make_actor("u-view", "viewer")
    operator = make_actor("u-op", "operator")

    started = time.perf_counter()
    for _ in range(ROUNDS):
        with contextlib.suppress(PermissionDeniedError):
            ensure_confirm_permission(viewer)
    deny_us = (time.perf_counter() - started) / ROUNDS * 1e6

    started = time.perf_counter()
    for _ in range(ROUNDS):
        ensure_confirm_permission(operator)
    pass_us = (time.perf_counter() - started) / ROUNDS * 1e6

    store = CustomerStore(":memory:").open()
    started = time.perf_counter()
    store.record_action(
        thread_id="t-26", kind="confirm_task", status="forbidden", detail="actor=u-view role=viewer"
    )
    audit_ms = (time.perf_counter() - started) * 1000

    rows = store.list_actions("t-26")
    store.close()

    print(f"被拒判定（含抛异常）   {deny_us:7.2f} µs / 次（{ROUNDS} 次平均）")
    print(f"放行判定               {pass_us:7.2f} µs / 次")
    print(f"拒绝审计写入           {audit_ms:7.2f} ms / 行")
    print(f"审计行数 {len(rows)}  status={rows[0]['status']}  tasks=0")


if __name__ == "__main__":
    main()
