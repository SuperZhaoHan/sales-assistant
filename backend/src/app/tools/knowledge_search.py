"""knowledge_search：把语料检索成「带引用编号的证据块」交给模型。

它只做检索，不做生成 —— 生成是摘要模板的事（1.3）。
返回的每一段都带 [n] 和 chunk_id，1.6 的「引用可反查」就靠这两个值。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.retriever import Retriever
from app.retrieval.store import Hit
from app.tools.base import Tool


class KnowledgeSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1, max_length=200, description="要检索的问题，用客户原话或业务术语"
    )
    top_k: int = Field(default=3, ge=1, le=5, description="召回条数，默认 3")
    # [TUTOR-WHY] 租户从参数进来、不由模型随手填：阶段 2 会改成从登录态注入，
    # [TUTOR-WHY] 模型只能决定「查什么」，不能决定「查谁的数据」
    tenant_id: str = Field(
        default="default", min_length=1, description="租户；跨租户内容不可见"
    )


def build_knowledge_search_tool(
    retriever: Retriever, *, min_score: float = 0.35, tenant_id: str = "default"
) -> Tool:
    async def handler(args: KnowledgeSearchArgs) -> str:
        # [TUTOR-WHY] 检索是纯内存点积（微秒级），不值得开线程；
        # [TUTOR-WHY] 换成 pgvector 后这一行才需要 asyncio.to_thread / async 驱动。
        hits = retriever.search(
            args.query, top_k=args.top_k, tenant_id=args.tenant_id or tenant_id, min_score=min_score
        )
        return render(args.query, hits, min_score=min_score)

    return Tool(
        name="knowledge_search",
        description=(
            "在产品资料、理赔流程、异议话术库里检索与客户问题相关的段落。"
            "返回带编号 [n] 与 chunk_id 的原文；低于相关性阈值的条目会被丢弃，"
            "若一条都没有说明知识库没有答案，不要凭常识作答。"
        ),
        args_schema=KnowledgeSearchArgs,
        handler=handler,
    )


def render(query: str, hits: tuple[Hit, ...], *, min_score: float) -> str:
    if not hits:
        # [TUTOR-WHY] 这句话是给模型看的指令，不是给客户看的：宁可说没有，也不能让它自己编
        return (
            f"知识库中未找到与「{query}」相关的内容（相关性阈值 {min_score}）。"
            "请如实告知需要人工确认，不要凭常识作答。"
        )
    # [TUTOR-JAVA] 这里 ≈ 把 List<Hit> 拼成一段文本；模型只能读字符串，读不到对象
    lines = [f"检索「{query}」命中 {len(hits)} 条："]
    for index, hit in enumerate(hits, 1):
        lines.append(
            f"[{index}] {hit.chunk.id}｜{hit.chunk.title}｜相关度 {hit.score:.2f}\n{hit.chunk.text}"
        )
    return "\n".join(lines)
