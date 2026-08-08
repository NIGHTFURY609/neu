"""Negotiation wire contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas import RiskSeverity


class NegotiationTier(StrEnum):
    PREFERRED = "preferred"
    ACCEPTABLE = "acceptable"
    WALK_AWAY = "walk_away"


class NegotiationPosition(BaseModel):
    position_id: str
    document_id: str
    redline_id: str
    risk_id: str
    clause_ref: str
    tier: NegotiationTier
    rank: int
    suggested_text: str
    rationale: str
    # What this rung gives up relative to the one above it.
    concession: str
    # The severity that remains if the counterparty accepts only this rung.
    residual_severity: RiskSeverity
    grounded_in_override: str | None = None
    confidence: float = 0.0
    created_at: datetime | None = None


class NegotiationLadder(BaseModel):
    redline_id: str
    clause_ref: str
    risk_id: str
    rule_id: str
    rule_version: int
    severity: RiskSeverity
    original_text: str
    positions: list[NegotiationPosition] = Field(default_factory=list)
    # True when the rule permits no overrides at all, so there is no middle rung. Stated
    # explicitly because "we found two options" and "the playbook allows no concession
    # here" are very different pieces of negotiating advice.
    no_fallback_available: bool = False
