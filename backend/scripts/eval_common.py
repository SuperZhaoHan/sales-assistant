"""评测脚本共用的 embedder 构造：默认哈希（不联网），--embedder real 才走真模型。

为什么不直接在脚本里 new OpenAICompatEmbedder：密钥的解析是异步的、还要走
``secret://`` 引用，绕过去就等于在脚本里复制一份启动逻辑。这里只做一件事——
把「配置驱动 + 密钥解析」这条唯一入口包成同步函数，给命令行脚本用。
"""

from __future__ import annotations

import asyncio
import os

from app.core.config import load_config
from app.retrieval.embedder import Embedder, HashingEmbedder
from app.retrieval.factory import build_embedder_from_settings
from app.security.secrets.factory import build_secret_resolver


def build_embedder(kind: str = "hash") -> Embedder:
    """``hash`` 返回本地哈希实现；``real`` 按 backend/.env 的配置调外部 embedding。"""
    if kind == "hash":
        return HashingEmbedder()
    if kind != "real":
        raise SystemExit(f"未知 embedder：{kind}（可选 hash / real）")
    # 先读配置：load_config 在 APP_ENV=local 时会顺带把 backend/.env 灌进环境变量，
    # 顺序反了就永远读不到 key（本脚本第一版就是这么挂的）
    settings = load_config()
    if not os.getenv("EMBEDDING_API_KEY"):
        raise SystemExit("EMBEDDING_API_KEY 为空：先在 backend/.env 里填硅基流动的 key")
    resolver = build_secret_resolver(settings)
    # 脚本是一次性进程：用 asyncio.run 拿完密钥就退出，不需要常驻事件循环
    return asyncio.run(build_embedder_from_settings(settings.embedding, resolver))


def describe(embedder: Embedder) -> str:
    """日志里显示「用的哪把尺子」，避免真/假数据混着比。"""
    model = getattr(embedder, "model", None) or "hash"
    return f"{type(embedder).__name__}({model})"


def effective_min_score(pack) -> float:
    """「当前生效的阈值」= 配置覆盖（local/prod）优先，没覆盖才回落 pack 的行业默认。

    评测必须报这个数：报 pack 里的 0.35 而线上跑 0.64，等于拿旧尺子的成绩单交差。
    """
    from app.tools.bootstrap import min_score_from

    settings = load_config()
    return min_score_from(pack, settings.retrieval.min_score)
