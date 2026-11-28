"""AI trade proposals for paper trading: stored record and REST contract.

A proposal is a *suggestion* the user may turn into a paper order; nothing here places
anything. Money is integer NT$, prices floats on the wire (``Decimal`` inside).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

ProposalStatus = Literal["pending", "accepted", "rejected", "expired", "superseded"]
ProposalAction = Literal["buy", "sell", "hold"]
CheckLevel = Literal["info", "adjusted", "blocked"]
Confidence = Literal["low", "medium", "high"]

# Keys of ``ProposalRecord.payload`` that are never served (audit only).
AUDIT_KEYS = ("raw_model_output",)


class ProposalRecord(BaseModel):
    """One stored proposal. ``payload`` holds the rest of the served object."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    account_id: UUID
    epoch: int = 0
    symbol: str
    name: str
    as_of: date
    created_at: datetime
    expires_at: datetime
    status: ProposalStatus = "pending"
    action: ProposalAction
    side: Literal["buy", "sell"] | None = None
    limit_price: Decimal | None = None
    lots: int | None = None
    order_id: UUID | None = None
    decided_at: datetime | None = None
    reject_reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ProposalOrder(BaseModel):
    side: Literal["buy", "sell"]
    order_type: Literal["limit"] = "limit"
    limit_price: float
    lots: int
    est_amount: int


class ProposalCheck(BaseModel):
    code: str
    level: CheckLevel
    message: str


class ProposalSignal(BaseModel):
    action: str
    score: float
    as_of: date


class ProposalSizing(BaseModel):
    equity: int
    cash_available: int
    position_lots: int
    risk_amount: int | None = None
    risk_pct: float | None = None
    position_pct: float | None = None


class Proposal(BaseModel):
    id: UUID
    account_id: UUID
    symbol: str
    name: str
    as_of: date
    created_at: datetime
    expires_at: datetime
    status: ProposalStatus
    action: ProposalAction
    order: ProposalOrder | None
    stop_loss: float | None
    take_profit: float | None
    horizon_days: int | None
    headline: str
    rationale: str
    key_evidence: list[str]
    risks: list[str]
    invalidation: list[str]
    evidence_refs: list[str]
    confidence: Confidence
    signal: ProposalSignal
    aligned: bool
    checks: list[ProposalCheck]
    sizing: ProposalSizing
    reference_price: float
    model: str
    prompt_version: str
    order_id: UUID | None
    decided_at: datetime | None
    reject_reason: str | None


class ProposalList(BaseModel):
    proposals: list[Proposal]
    has_more: bool


class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)


class ProposalReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=200)
