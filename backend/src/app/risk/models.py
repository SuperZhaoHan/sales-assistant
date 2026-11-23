from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class RiskSubject:
    subject_id: str
    tenant_id: str | None = None
    ip: str | None = None


@dataclass
class RiskContext:
    subject: RiskSubject
    action: str
    resource: str | None = None
    model: str | None = None
    input_text: str = ""
    estimated_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def usage_key(self) -> str:
        return f"{self.subject.tenant_id or self.subject.subject_id}:{self.action}"

    @property
    def budget_key(self) -> str:
        return self.subject.tenant_id or "global"


@dataclass(frozen=True)
class RiskResult:
    rule: str
    allowed: bool
    reason: str
    risk_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    decision: Literal["allow", "deny", "challenge"]
    risk_score: float
    reasons: tuple[str, ...]
    audit_id: str
    results: tuple[RiskResult, ...] = ()
