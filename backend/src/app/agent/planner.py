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

    actions: tuple[str, ...] = ("customer_profile", "knowledge_search")

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str = "fake-model",
        max_tokens: int = 160,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens
        self._cost = CostCalculator()

    async def __call__(self, state: AgentState) -> Decision:
        if state.intent is Intent.CHITCHAT:
            text, tokens, cost = await self._think(state, "用户只是打招呼，简短回一句即可")
            return Decision(
                answer=text, input_tokens=tokens[0], output_tokens=tokens[1], cost_usd=cost
            )

        step_index = len(state.steps)
        if step_index < len(self.actions):
            action = self.actions[step_index]
            text, tokens, cost = await self._think(
                state, f"第 {step_index + 1} 步：准备调用工具 {action}"
            )
            return Decision(
                thought=text,
                action=action,
                args={"query": state.task[:80]},
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=cost,
            )

        text, tokens, cost = await self._think(
            state, "根据上面的观察给出结论，不要编造观察里没有出现的数据"
        )
        return Decision(answer=text, input_tokens=tokens[0], output_tokens=tokens[1], cost_usd=cost)

    async def _think(
        self, state: AgentState, instruction: str
    ) -> tuple[str, tuple[int, int], float]:
        observations = [step.observation for step in state.steps if step.observation]
        evidence = "\n".join(f"{index}. {text}" for index, text in enumerate(observations, 1))
        request = LLMRequest(
            messages=(
                LLMMessage(
                    role="user",
                    content=(
                        f"任务：{state.task}\n"
                        f"已收集的观察：\n{evidence or '（暂无，需要先查）'}\n"
                        f"{instruction}"
                    ),
                ),
            ),
            model=self._model,
            max_tokens=self._max_tokens,
        )
        response = await self._provider.complete(request)
        usage = response.usage
        cost = self._cost.calculate(self._model, usage.input_tokens, usage.output_tokens)
        return response.text, (usage.input_tokens, usage.output_tokens), cost
