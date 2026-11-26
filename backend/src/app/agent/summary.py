"""把「查档结果」装配成一次模型请求。

为什么单独一层：
- 端点只负责 HTTP 编解码；装配提示词、决定调不调模型放在函数里，能单独测；
- 「查不到就不调模型」是这一层的决定，不是端点的 if 判断 —— 以后换入口
  （定时任务、内部脚本）也自动守同一条规则，不会有人漏写。

字段对齐：模板里的三个占位符由 ``Brief.variables()`` 提供，名字写错会在
``render()`` 里抛 ValidationError，而不是渲染出半成品提示词。
"""

from __future__ import annotations

from app.agent.brief import Brief
from app.domains.registry import DomainPack
from app.llm.base import LLMMessage, LLMRequest

# 用户消息只有一句：四段内容（角色/任务/约束/格式）全在 system 里，
# 用户消息带多了反而会和模板里的约束打架。
SUMMARY_INSTRUCTION = "请按模板规定的小标题，为这位客户写一份续保跟进摘要。"


def build_summary_request(
    pack: DomainPack,
    brief: Brief,
    *,
    model: str,
    max_tokens: int,
    temperature: float | None = None,
) -> LLMRequest | None:
    """装配请求。查不到客户时返回 ``None``：调用方据此跳过模型调用、token 花 0。"""
    if not brief.found:
        return None
    # [TUTOR-WHY] 用 pack.entry 而不是硬编码模板名：换行业只换 pack，代码不改
    system = pack.entry.render(brief.variables())
    return LLMRequest(
        messages=(LLMMessage(role="user", content=SUMMARY_INSTRUCTION),),
        model=model,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        # 审计日志里能还原「这条摘要出自哪版模板、哪个客户」
        metadata={
            "prompt_template": pack.entry.fingerprint(),
            "customer_name": brief.customer_name,
        },
    )
