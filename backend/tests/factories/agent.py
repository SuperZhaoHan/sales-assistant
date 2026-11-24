from __future__ import annotations

from typing import Any

from app.agent.loop import Decision
from app.agent.stop import Limits


def make_decision(
    *,
    thought: str = "",
    action: str | None = None,
    args: dict[str, Any] | None = None,
    answer: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
) -> Decision:
    return Decision(
        thought=thought,
        action=action,
        args=args or {},
        answer=answer,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def make_limits(
    *,
    max_steps: int = 6,
    max_tokens: int = 3000,
    max_cost_usd: float | None = None,
    timeout_seconds: float = 30.0,
) -> Limits:
    return Limits(
        max_steps=max_steps,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        timeout_seconds=timeout_seconds,
    )
