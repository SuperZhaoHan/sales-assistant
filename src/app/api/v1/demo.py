from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import ValidationError
from app.domain.errors import InsufficientBalanceError
from app.risk.errors import RiskDeniedError

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/business-error")
async def business_error() -> dict[str, str]:
    raise InsufficientBalanceError(details={"required": 100, "available": 10})


@router.get("/validation-error")
async def validation_error() -> dict[str, str]:
    raise ValidationError(details={"field": "amount", "reason": "must be positive"})


@router.get("/risk-error")
async def risk_error() -> dict[str, str]:
    raise RiskDeniedError(details={"reasons": ["demo"]})


@router.get("/internal-error")
async def internal_error() -> dict[str, str]:
    raise RuntimeError("intentional demo failure")
