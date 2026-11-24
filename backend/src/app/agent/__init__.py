"""Agent 引擎。

阶段 0 只有三件事：状态形状（``state``）、停机判据（``stop``）、最小循环（``loop``），
外加一个可复现的规划器（``planner``）。工具层、确认、写操作都在后面的阶段接进来。
"""

from __future__ import annotations

from app.agent.intent import classify_intent
from app.agent.loop import Decision, Planner, ToolExecutor, run_agent
from app.agent.state import Intent, TaskStage
from app.agent.stop import Limits, check_stop

__all__ = [
    "Decision",
    "Intent",
    "Limits",
    "Planner",
    "TaskStage",
    "ToolExecutor",
    "check_stop",
    "classify_intent",
    "run_agent",
]
