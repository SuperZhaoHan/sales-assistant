"""启动期装配：开存储、灌种子、建索引、把工具登记进注册表。

放在项目自己的模块里，不直接写进 ``lifespan``：以后加工具只要改这里一行，
``main.py`` 保持「只负责编排启动顺序」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ValidationError
from app.domains.registry import DomainPack
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


def build_retriever(corpus_dir: str | Path) -> Retriever:
    """把 ``corpus/*.yaml`` 全部灌进索引。语料按行业拆文件即可。"""
    directory = Path(corpus_dir)
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise ValidationError(f"语料目录为空：{directory.name}", details={"path": str(directory)})

    retriever = Retriever()
    for file in files:
        retriever.index(load_chunks(file))
    return retriever


def min_score_from(pack: DomainPack) -> float:
    """阈值来自 pack，不写死在代码里 —— 换行业时这个值一定不一样。"""
    thresholds = pack.rules.get("thresholds") or {}
    return float(thresholds.get("retrieval_min_score", DEFAULT_MIN_SCORE))


def build_toolset(
    store: CustomerStore, retriever: Retriever, *, min_score: float
) -> tuple[Tool, ...]:
    """工具清单。顺序 = 给模型看的顺序，也是注册表里的插入序。"""
    return (
        build_customer_profile_tool(store),
        build_knowledge_search_tool(retriever, min_score=min_score),
    )


def register_domain_tools(
    pack: DomainPack, store: CustomerStore, retriever: Retriever
) -> tuple[str, ...]:
    """登记业务工具，返回注册表里的全部工具名。

    已经登记过就跳过：测试里会反复创建 app，启动期重复登记不该被当成错误。
    """
    existing = {tool.name for tool in list_tools()}
    for tool in build_toolset(store, retriever, min_score=min_score_from(pack)):
        # [TUTOR-WHY] 重复登记跳过，而不是报错：测试里会反复建 app
        if tool.name not in existing:
            register(tool)
            existing.add(tool.name)
    return tuple(tool.name for tool in list_tools())


def bootstrap_domain(pack: DomainPack, store_path: str | Path) -> Bootstrap:
    store = open_store(store_path, pack.root / "seed")
    retriever = build_retriever(pack.root / "corpus")
    tools = register_domain_tools(pack, store, retriever)
    return Bootstrap(store=store, retriever=retriever, tools=tools, min_score=min_score_from(pack))
