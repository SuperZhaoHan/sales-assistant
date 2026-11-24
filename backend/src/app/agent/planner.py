"""规划器：把「下一步做什么」抽象成一个可替换的 callable。

阶段 0 提供三个，各有明确用途：

- ``ScriptedPlanner``：照剧本演，用来精确复现某个停机原因；
- ``ConstantPlanner``：永远给同一个动作，用来证明没有刹车就会停不下来；
- ``FakeLLMPlanner``：真的调一次 provider，走完「意图 → 查工具 → 出答复」三步。
  测试里 provider 是 ``FakeLLMProvider``，token 数是真算的，但不花钱也不联网。

换成真模型时只换 provider，不改这个类——这是「可替换边界」在 Agent 层的落点。
"""

from __future__ import annotations

from app.agent.loop import Decision
from app.agent.state import AgentState, Intent
from app.core.errors import ValidationError
from app.llm.base import LLMMessage, LLMProvider, LLMRequest
from app.llm.cost import CostCalculator


class ScriptedPlanner:
    """照剧本演；剧本用完还在被调用，说明循环没在该停的地方停，直接失败。"""

    def __init__(self, decisions: list[Decision]) -> None:
        if not decisions:
            raise ValidationError("剧本不能为空", details={"field": "decisions"})
        self._decisions = list(decisions)
        self.calls = 0

    async def __call__(self, state: AgentState) -> Decision:
        if self.calls >= len(self._decisions):
            raise AssertionError(
                f"剧本只有 {len(self._decisions)} 步，循环却请求了第 {self.calls + 1} 步"
            )
        decision = self._decisions[self.calls]
        self.calls += 1
        return decision


class ConstantPlanner:
    """永远返回同一个动作。用来验证 ``max_steps``：没有刹车它就会一直转。"""

    def __init__(self, decision: Decision) -> None:
        self._decision = decision
        self.calls = 0

    async def __call__(self, state: AgentState) -> Decision:
        self.calls += 1
        return self._decision


class FakeLLMPlanner:
    """三步走：查客户 → 查知识 → 出答复，每一步都真调一次 provider。

    对话类（``chitchat``）不走工具，直接一次调用收尾，避免为了打招呼也查库。
    """

    # 类属性：所有实例共用一份。用 tuple（不可变）而不是 list，防止被就地修改。
    # ``tuple[str, ...]`` 读作「元素是 str、长度不固定」。
    actions: tuple[str, ...] = ("customer_profile", "knowledge_search")

    def __init__(
        self,
        provider: LLMProvider,  # 必传：由调用方注入，本类不自己造 provider
        *,
        model: str = "fake-model",  # ``*`` 之后的参数只能按名字传，避免两个字符串传反
        max_tokens: int = 160,  # 单次输出上限；假模型不用它，真模型会用
    ) -> None:
        self._provider = provider  # 赋值即定义字段，Python 没有「字段声明」这一行
        self._model = model
        self._max_tokens = max_tokens
        self._cost = CostCalculator()  # fake-model 不在价格表里，会走默认单价

    async def __call__(self, state: AgentState) -> Decision:
        # 定义了 __call__，实例才能像函数一样写 planner(state)，不用 .plan()
        if state.intent is Intent.CHITCHAT:
            # 枚举是单例，用 is 比较；闲聊不查库，一次调用就直接收尾
            text, tokens, cost = await self._think(state, "用户只是打招呼，简短回一句即可")
            # 一次解包三个返回值：文本、(输入 token, 输出 token)、费用
            return Decision(
                answer=text,  # 有 answer 之后，循环下一轮就判 done
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=cost,
            )

        step_index = len(state.steps)  # 进度从状态读，不从对象内部读，中断后可恢复
        if step_index < len(self.actions):  # 前两步调工具，第三步出答复
            action = self.actions[step_index]  # 第 0 步查客户，第 1 步查知识
            text, tokens, cost = await self._think(
                state,
                f"第 {step_index + 1} 步：准备调用工具 {action}",  # f-string 里可放表达式
            )
            return Decision(
                thought=text,  # 存下这次的模型输出，便于回放排查
                action=action,  # 告诉循环「去调这个工具」
                args={"query": state.task[:80]},  # 切片不报越界，与 Java 的 substring 不同
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=cost,
            )

        # 第三步：不再调工具，直接出答复
        text, tokens, cost = await self._think(
            state, "根据上面的观察给出结论，不要编造观察里没有出现的数据"
        )
        return Decision(answer=text, input_tokens=tokens[0], output_tokens=tokens[1], cost_usd=cost)

    async def _think(
        self, state: AgentState, instruction: str
    ) -> tuple[str, tuple[int, int], float]:  # 返回「文本 + (输入,输出) token + 费用」
        # 列表推导 = 遍历 + 过滤 + 取值，一行完成；≈ Java 的 stream().filter().map()
        # 注意 ``if step.observation`` 连空字符串也会过滤掉，是当前已知的小缺陷
        observations = [step.observation for step in state.steps if step.observation]
        # enumerate(..., 1) 带序号遍历、从 1 开始；join 把列表拼成多行文本
        evidence = "\n".join(f"{index}. {text}" for index, text in enumerate(observations, 1))
        request = LLMRequest(
            messages=(  # 元组而非列表：LLMRequest 是 frozen dataclass，内部保持不可变
                LLMMessage(
                    role="user",  # 目前只放一条用户消息，四段模板（system）阶段 1 才接进来
                    content=(
                        f"任务：{state.task}\n"
                        f"已收集的观察：\n{evidence or '（暂无，需要先查）'}\n"  # 空则兜底文案
                        f"{instruction}"
                    ),
                ),
            ),
            model=self._model,
            max_tokens=self._max_tokens,
        )
        # 只依赖 LLMProvider 接口，所以把假 provider 换成真 provider 时本类一行都不用改
        response = await self._provider.complete(request)
        usage = response.usage  # provider 返回的 token 用量
        cost = self._cost.calculate(self._model, usage.input_tokens, usage.output_tokens)
        return response.text, (usage.input_tokens, usage.output_tokens), cost
