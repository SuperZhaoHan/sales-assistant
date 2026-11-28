"""2.1 状态图：把「while True 里各模块自己决定下一步」换成显式节点 + 条件边。

为什么现在做：从 2.2 起，每一条能力都要求「可停、可重入、可恢复」——
人工确认要能停在半路等人，进程崩了要能从断点接着跑。
隐式循环做不到「停在第几步」，只有显式状态机做得到。

六节点主线：

    classify_intent → fetch_customer → retrieve_knowledge → draft_advice
                   ↘（闲聊/没给客户名）              ↓
                                              await_confirm → commit_task

这一节只搭骨架：依赖全是注入的 callable，不调模型、不碰网络，
所以「同输入两次节点序列一致」可以在毫秒级断言。
"""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.intent import classify_intent
from app.agent.state import Intent, TaskStage
from app.core.errors import BusinessRuleError, InternalError


# [TUTOR-WHY] trace 用 Annotated + operator.add 声明成「累加字段」：
# [TUTOR-WHY] 每个节点只返回自己那一项，LangGraph 会帮我们拼接，而不是后者覆盖前者。
# [TUTOR-JAVA] ≈ 一个 onEach 往里 append 的累加器（List.addAll），不是每步 new 一个新 List。
class GraphState(TypedDict, total=False):
    """图里流动的状态。``total=False`` 表示每个键都可以暂时缺席。

    ``task`` 是唯一的必填输入；其余都由节点逐步填上——这就是「状态随流程长出来」。
    """

    task: str
    customer_name: str
    intent: str
    stage: str
    customer: str
    knowledge: str
    advice: str
    task_title: str
    target_task_id: str
    confirmed: bool
    task_id: str
    trace: Annotated[list[str], operator.add]


@dataclass(frozen=True)
class GraphDeps:
    """图的三个外部依赖。全都是 async callable，测试里换成记账用的假函数即可。

    为什么不在节点里直接 import 工具：节点一旦自己去拿依赖，
    「只跑图不连库」这件事就做不到，测试会退化成集成测试。
    """

    lookup_customer: Callable[[str], Awaitable[str]]
    retrieve_knowledge: Callable[[str], Awaitable[str]]
    commit_task: Callable[[Mapping[str, str]], Awaitable[str]]
    # [TUTOR-WHY] 删除相关依赖默认 None：没注入就代表这项能力不存在，
    # 走到删除路径时直接报 InternalError，而不是悄悄什么都不做。
    find_task: Callable[[str], Awaitable[str | None]] | None = None
    delete_task: Callable[[str], Awaitable[None]] | None = None
    intents: Mapping[str, tuple[str, ...]] | None = None


NODE_NAMES: tuple[str, ...] = (
    "classify_intent",
    "fetch_customer",
    "retrieve_knowledge",
    "draft_advice",
    "await_confirm",
    "commit_task",
)


def thread_config(thread_id: str) -> dict[str, Any]:
    """LangGraph 的「会话钥匙」。同一个 thread_id + 同一个 saver 才能续上状态。"""

    return {"configurable": {"thread_id": thread_id}}


def build_graph(deps: GraphDeps, *, checkpointer: BaseCheckpointSaver | None = None) -> Any:
    """编译出可执行的图。``checkpointer`` 决定「暂停后还能不能接着跑」。

    2.1 用 ``InMemorySaver``；2.2 换成 Postgres，节点代码一行都不改。
    """

    async def classify_intent_node(state: GraphState) -> dict[str, Any]:
        # [TUTOR-JAVA] ≈ 在状态机入口做一次 route 判定，只不过判定结果写回了状态。
        intent = classify_intent(state["task"], deps.intents)
        return {
            "intent": intent.value,
            "stage": TaskStage.DRAFT.value,
            "trace": ["classify_intent"],
        }

    async def fetch_customer_node(state: GraphState) -> dict[str, Any]:
        text = await deps.lookup_customer(state["customer_name"])
        return {"customer": text, "trace": ["fetch_customer"]}

    async def retrieve_knowledge_node(state: GraphState) -> dict[str, Any]:
        text = await deps.retrieve_knowledge(state["task"])
        return {"knowledge": text, "trace": ["retrieve_knowledge"]}

    async def resolve_target_node(state: GraphState) -> dict[str, Any]:
        # 删除类指令只说了「把待办删了」，没给任务号——先替用户认出要删哪条。
        if deps.find_task is None:
            raise InternalError("删除能力未装配（缺少 find_task 依赖）")
        name = state.get("customer_name") or ""
        task_id = await deps.find_task(name)
        if task_id is None:
            # 找不到就别往下走：确认框里写不出去"删哪条"，弹给用户也没法点
            raise BusinessRuleError(
                "该客户没有可撤销的待办",
                details={"customer_name": name, "code": "NO_OPEN_TASK"},
            )
        return {"target_task_id": task_id, "trace": ["resolve_target"]}

    async def draft_advice_node(state: GraphState) -> dict[str, Any]:
        # 2.1 的草稿是模板拼的，故意的：先证明「图能跑通」，2.3 再接模型。
        name = state.get("customer_name") or "该客户"
        if state["intent"] == Intent.CREATE_TASK.value:
            title = f"为{name}确认续保"
        elif state["intent"] == Intent.DELETE_TASK.value:
            title = f"撤销{name}的待办 {state.get('target_task_id', '')}"
        else:
            title = f"整理{name}的答复"
        lines = [f"跟进建议（{name}）：", f"- 下一步：{title}"]
        if state.get("customer"):
            lines.append(f"- 客户现状：{state['customer']}")
        if state.get("knowledge"):
            lines.append(f"- 政策依据：{state['knowledge']}")
        return {
            "advice": "\n".join(lines),
            "task_title": title,
            "trace": ["draft_advice"],
        }

    async def await_confirm_node(state: GraphState) -> dict[str, Any]:
        # [TUTOR-WHY] interrupt() 会把整个图「冻住」并把载荷交给调用方；恢复时这个节点
        # [TUTOR-WHY] 从头再执行一遍，interrupt 返回调用方传来的值。
        # [TUTOR-WHY] 所以 interrupt 之前的代码必须无副作用——重复执行不能写两次库。
        action = "撤销任务" if state["intent"] == Intent.DELETE_TASK.value else "创建任务"
        decision = interrupt(
            {"question": f"确认{action}「{state['task_title']}」？", "advice": state["advice"]}
        )
        return {
            "confirmed": bool(decision),
            "stage": TaskStage.AWAITING_CONFIRM.value,
            "trace": ["await_confirm"],
        }

    async def commit_task_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        # 「落实决定」：confirmed 才写，拒绝也走这个节点，好处是「终态只有一处写」。
        if not state.get("confirmed", False):
            return {"stage": TaskStage.ABORTED.value, "trace": ["commit_task"]}
        # [TUTOR-WHY] 幂等键要用 thread_id 参与计算，所以节点必须读 config；
        # [TUTOR-WHY] thread_id 不在 state 里，它是「会话的身份」，由调用方持有。
        thread_id = str((config.get("configurable") or {}).get("thread_id", ""))
        if state["intent"] == Intent.DELETE_TASK.value:
            if deps.delete_task is None:
                raise InternalError("删除能力未装配（缺少 delete_task 依赖）")
            deleted = state["target_task_id"]
            await deps.delete_task(deleted)
            return {
                "task_id": deleted,
                "stage": TaskStage.COMMITTED.value,
                "trace": ["commit_task"],
            }
        task_id = await deps.commit_task(
            {
                "thread_id": thread_id,
                "customer_name": state.get("customer_name", ""),
                "title": state["task_title"],
                "advice": state["advice"],
            }
        )
        return {"task_id": task_id, "stage": TaskStage.COMMITTED.value, "trace": ["commit_task"]}

    def route_after_classify(state: GraphState) -> str:
        # 返回的是「边上的标签」，不是节点名；映射表在 add_conditional_edges 里。
        if state["intent"] == Intent.CHITCHAT.value:
            return "done"
        if state["intent"] == Intent.DELETE_TASK.value:
            return "target"
        if state.get("customer_name"):
            return "customer"
        return "knowledge"

    def route_after_draft(state: GraphState) -> str:
        # 只有「建任务」需要人工确认；纯问答问完就答，不打扰用户。
        # [TUTOR-WHY] 判据是「这一轮有没有写动作」，不是「哪几个意图」：
        # 新增写意图只改这里一处，而不是到处补 if。
        if state["intent"] in (Intent.CREATE_TASK.value, Intent.DELETE_TASK.value):
            return "confirm"
        return "done"

    builder: StateGraph = StateGraph(GraphState)
    builder.add_node("classify_intent", classify_intent_node)
    builder.add_node("fetch_customer", fetch_customer_node)
    builder.add_node("retrieve_knowledge", retrieve_knowledge_node)
    builder.add_node("resolve_target", resolve_target_node)
    builder.add_node("draft_advice", draft_advice_node)
    builder.add_node("await_confirm", await_confirm_node)
    builder.add_node("commit_task", commit_task_node)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        route_after_classify,
        {
            "customer": "fetch_customer",
            "target": "resolve_target",
            "knowledge": "retrieve_knowledge",
            "done": END,
        },
    )
    # 知识检索是公共步骤：有客户档案也照样要查政策，两条路径在这里汇合。
    builder.add_edge("resolve_target", "draft_advice")
    builder.add_edge("fetch_customer", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "draft_advice")
    builder.add_conditional_edges(
        "draft_advice", route_after_draft, {"confirm": "await_confirm", "done": END}
    )
    builder.add_edge("await_confirm", "commit_task")
    builder.add_edge("commit_task", END)

    return builder.compile(checkpointer=checkpointer)


def resume(value: object) -> Command:
    """恢复一次暂停。``value`` 就是 ``interrupt()`` 的返回值。"""

    return Command(resume=value)
