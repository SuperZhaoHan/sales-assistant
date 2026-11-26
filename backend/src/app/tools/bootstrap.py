"""启动期装配：开存储、灌种子、建索引、把工具登记进注册表。

放在项目自己的模块里，不直接写进 ``lifespan``：以后加工具只要改这里一行，
``main.py`` 保持「只负责编排启动顺序」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ValidationError
from app.domains.registry import DomainPack
from app.retrieval.embedder import Embedder
from app.retrieval.retriever import Retriever, load_chunks
from app.store.sqlite_store import CustomerStore
from app.tools.base import Tool
from app.tools.customer_profile import build_customer_profile_tool
from app.tools.knowledge_search import build_knowledge_search_tool
from app.tools.registry import list_tools, register

DEFAULT_MIN_SCORE = 0.35


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """启动完成后的几样东西。冻结：不让某个 handler 顺手把 store 换掉。"""

    store: CustomerStore
    retriever: Retriever
    tools: tuple[str, ...]
    min_score: float


def open_store(store_path: str | Path, seed_dir: str | Path) -> CustomerStore:
    """开库 + 建表 + 灌种子；``store_path`` 是 ``:memory:`` 时跳过建目录。"""
    if str(store_path) != ":memory:":
        Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    store = CustomerStore(store_path).open()
    store.load_seed(seed_dir)
    return store


def build_retriever(corpus_dir: str | Path, embedder: Embedder | None = None) -> Retriever:
    """把 ``corpus/*.yaml`` 全部灌进索引。语料按行业拆文件即可。"""
    directory = Path(corpus_dir)
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise ValidationError(f"语料目录为空：{directory.name}", details={"path": str(directory)})

    # 不传 embedder 就用哈希版：CI 里不许联网，测试必须能在无密钥环境跑绿
    retriever = Retriever(embedder)
    for file in files:
        retriever.index(load_chunks(file))
    return retriever


def min_score_from(pack: DomainPack, override: float | None = None) -> float:
    """阈值优先取配置覆盖，没覆盖才回落到 pack。

    为什么要有覆盖：分数尺度由 embedding 决定（哈希 256 维 vs bge-m3 1024 维
    同一批题的顶分会整体抬高），而行业包只该管业务口径。不覆盖就等于换模型
    时悄悄改了「答不答」的判断标准。
    """
    if override is not None:
        return float(override)
    thresholds = pack.rules.get("thresholds") or {}
    return float(thresholds.get("retrieval_min_score", DEFAULT_MIN_SCORE))


def refusal_text_from(pack: DomainPack) -> str | None:
    """拒答话术也来自 pack；没配就返回 None，由工具用默认文案。"""
    refusal = pack.rules.get("refusal") or {}
    text = refusal.get("text") if isinstance(refusal, dict) else None
    return str(text).strip() if text else None


def _optional_refusal(pack: DomainPack) -> dict[str, str]:
    # [TUTOR-WHY] 没配就不传，交给工具用默认文案：不把 None 传进去让下游判断
    text = refusal_text_from(pack)
    return {"refusal_text": text} if text else {}


def build_toolset(
    pack: DomainPack, store: CustomerStore, retriever: Retriever, *, min_score: float
) -> tuple[Tool, ...]:
    """工具清单。顺序 = 给模型看的顺序，也是注册表里的插入序。"""
    return (
        build_customer_profile_tool(store),
        build_knowledge_search_tool(
            retriever,
            min_score=min_score,
            **_optional_refusal(pack),
        ),
    )


def register_domain_tools(
    pack: DomainPack,
    store: CustomerStore,
    retriever: Retriever,
    *,
    min_score: float | None = None,
) -> tuple[str, ...]:
    """登记业务工具，返回注册表里的全部工具名。

    已经登记过就跳过：测试里会反复创建 app，启动期重复登记不该被当成错误。
    """
    existing = {tool.name for tool in list_tools()}
    effective = min_score_from(pack, min_score)
    for tool in build_toolset(pack, store, retriever, min_score=effective):
        # [TUTOR-WHY] 重复登记跳过，而不是报错：测试里会反复建 app
        if tool.name not in existing:
            register(tool)
            existing.add(tool.name)
    return tuple(tool.name for tool in list_tools())


def bootstrap_domain(
    pack: DomainPack,
    store_path: str | Path,
    embedder: Embedder | None = None,
    *,
    min_score: float | None = None,
) -> Bootstrap:
    store = open_store(store_path, pack.root / "seed")
    retriever = build_retriever(pack.root / "corpus", embedder)
    tools = register_domain_tools(pack, store, retriever, min_score=min_score)
    effective = min_score_from(pack, min_score)
    return Bootstrap(store=store, retriever=retriever, tools=tools, min_score=effective)
