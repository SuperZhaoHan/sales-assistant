from __future__ import annotations

# USD per 1M tokens: (input, output). Extend as new models are approved.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
    # DeepSeek 有高峰/空闲两档价，取高峰价（贵的那档）估成本，避免低估
    "deepseek-flash": (0.30, 1.20),
    "deepseek-v4-pro": (1.32, 3.96),
    "deepseek-chat": (0.28, 0.42),
    "deepseek-reasoner": (0.28, 0.42),
}


class CostCalculator:
    def __init__(self, default_input: float = 4.0, default_output: float = 20.0) -> None:
        self._default_input = default_input
        self._default_output = default_output

    def calculate(self, model: str, input_tokens: int, output_tokens: int) -> float:
        input_price, output_price = MODEL_PRICES.get(
            model,
            (self._default_input, self._default_output),
        )
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
