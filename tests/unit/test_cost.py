from __future__ import annotations

from app.llm.cost import CostCalculator


def test_known_model_price() -> None:
    calculator = CostCalculator()

    assert calculator.calculate("claude-opus-5-5", 1_000_000, 1_000_000) == 24.0


def test_unknown_model_uses_defaults() -> None:
    calculator = CostCalculator(default_input=1.0, default_output=2.0)

    assert calculator.calculate("unknown-model", 1_000_000, 1_000_000) == 3.0


def test_partial_tokens() -> None:
    calculator = CostCalculator()

    assert calculator.calculate("claude-haiku-5-5", 500_000, 0) == 0.05
