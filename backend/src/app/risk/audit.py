from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.risk.models import RiskContext, RiskDecision

_audit_logger = structlog.get_logger("audit")


class AuditLogger:
    """Append-only audit stream for risk and LLM decisions.

    Callers must pass only non-sensitive fields. Prompt and response bodies are
    never sent here.
    """

    def log(self, event: str, **fields: Any) -> str:
        audit_id = uuid.uuid4().hex
        _audit_logger.info(event, audit_id=audit_id, **fields)
        return audit_id

    def risk_decision(self, decision: RiskDecision, context: RiskContext) -> str:
        return self.log(
            "risk_decision",
            decision=decision.decision,
            allowed=decision.allowed,
            risk_score=decision.risk_score,
            reasons=list(decision.reasons),
            rules=[result.rule for result in decision.results],
            action=context.action,
            subject_id=context.subject.subject_id,
            tenant_id=context.subject.tenant_id,
            model=context.model,
            input_chars=len(context.input_text),
            estimated_tokens=context.estimated_tokens,
        )
