"""1.3b 演示：打一次 /v1/summary，把「调模型」和「不调模型」两条路的数字都打出来。

跑法：``.venv/bin/python scripts/demo_summary.py``
用 fake provider，不花真钱；数字是真实的 token 估算与端到端耗时。
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app

CASES = ("张伟", "王芳", "张三")


def main() -> None:
    app = create_app(load_config("test"))
    with TestClient(app) as client:
        contacts = app.state.store.contact_count()
        chunks = app.state.retriever.size
        print(f"入库：{contacts} 位客户，语料 {chunks} 条\n")
        for name in CASES:
            started = time.perf_counter()
            body = client.post("/v1/summary", json={"customer_name": name}).json()["data"]
            wall_ms = (time.perf_counter() - started) * 1000
            print(f"— {name} —")
            print(
                f"found={body['found']} llm_called={body['llm_called']} "
                f"tokens={body['usage']['total_tokens']} "
                f"cost=${body['usage']['cost_usd']:.6f} "
                f"latency={body['latency_ms']} ms（含 HTTP 往返 {wall_ms:.1f} ms）"
            )
            print(f"facts={body['facts']}")
            if body["llm_called"]:
                print(f"text={body['text']}")
            else:
                print(f"text（固定话术，未调模型）={body['text']}")
            print()

        print("— 张伟这次实际发给模型的 system prompt 头 12 行 —")
        request = app.state.domain.entry.render(
            {"profile": "…", "assets": "…", "history": "…", "facts": "F1=…"}
        )
        for line in request.splitlines()[:12]:
            print(line)
        print("\n（上面是占位符版，真实调用里三段被填成张伟的资料与资产）")


if __name__ == "__main__":
    main()
