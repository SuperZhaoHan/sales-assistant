from __future__ import annotations

from typing import Any


class RecordingToolExecutor:
    """假的工具执行器：记下每次调用，按名字返回预置结果。

    它同时是断言依据——「工具被调了几次、参数对不对」直接看 ``calls``，
    不需要去 mock 内部实现。
    """

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses or {}

    async def __call__(self, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, dict(args)))
        return self._responses.get(name, f"<{name}> 无数据")
