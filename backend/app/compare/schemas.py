"""The comparison wire contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.compare.textdiff import DiffSpan
from app.schemas import FactType, RiskFlagStatus, RiskSeverity


class DocumentRef(BaseModel):
    document_id: str
    filename: str
    title: str | None = None
    version: int = 1
    jurisdiction: str | None = None
    uploaded_at: datetime | None = None


class ClauseAlignment(BaseModel):
    left_ref: str | None
    right_ref: str | None
    heading: str = ""
    status: Literal["identical", "modified", "added", "removed", "renumbered"]
    # The composite pairing score. Shown as a confidence, not as a similarity.
    alignment_score: float = 0.0
    # Normalized body similarity on its own — the number worth showing a human.
    similarity: float = 0.0
    diff: list[DiffSpan] | None = None


class RiskDelta(BaseModel):
    """How one rule's outcome changed between the two documents.

    Keyed on `(rule_id, alignment)` rather than on `clause_ref`: refs move between
    versions, which is the whole reason alignment exists. Keying on the ref would report
    every renumbered clause as one risk disappearing and an identical one appearing.

    This is also the shape `/documents/{id}/risk-preview` returns for the
    "how would this fare under another jurisdiction's playbook" feature — same question,
    different second document, so the UI renders one component for both.
    """

    change: Literal["appeared", "resolved", "severity_changed", "suppression_changed", "unchanged"]
    rule_id: str
    left_risk_id: str | None = None
    right_risk_id: str | None = None
    left_ref: str | None = None
    right_ref: str | None = None
    left_severity: RiskSeverity | None = None
    right_severity: RiskSeverity | None = None
    left_status: RiskFlagStatus | None = None
    right_status: RiskFlagStatus | None = None
    note: str = ""


class RedlineOutcome(BaseModel):
    """Whether a redline we suggested on the left actually landed on the right.

    For version comparison this is the single most useful thing in the response: it
    answers "did counsel take our edit?", which is otherwise a manual read-through.
    """

    redline_id: str
    clause_ref: str
    outcome: Literal["applied", "partially_applied", "not_applied", "clause_removed"]
    similarity: float = 0.0
    right_ref: str | None = None


class FactDelta(BaseModel):
    fact_type: FactType
    left_ref: str | None = None
    right_ref: str | None = None
    left_value: dict | None = None
    right_value: dict | None = None
    change: Literal["added", "removed", "changed", "unchanged"]


class ComparisonTotals(BaseModel):
    clauses_identical: int = 0
    clauses_modified: int = 0
    clauses_added: int = 0
    clauses_removed: int = 0
    risks_appeared: int = 0
    risks_resolved: int = 0
    risks_severity_changed: int = 0


class ComparisonResult(BaseModel):
    left: DocumentRef
    right: DocumentRef
    pairing: Literal["version", "cross_document"]
    clauses: list[ClauseAlignment] = Field(default_factory=list)
    risk_deltas: list[RiskDelta] = Field(default_factory=list)
    redline_outcomes: list[RedlineOutcome] = Field(default_factory=list)
    fact_deltas: list[FactDelta] = Field(default_factory=list)
    totals: ComparisonTotals = Field(default_factory=ComparisonTotals)
    # Things the reader needs in order to trust the rest: unaligned preamble text, OCR
    # floors, pairings the machine is not confident about.
    caveats: list[str] = Field(default_factory=list)


class RiskPreview(BaseModel):
    """Re-evaluating one document under a different jurisdiction's playbook.

    `unmapped_rules` is not optional decoration. A tool that claims to have re-evaluated
    a contract under a foreign playbook without saying which of its rules have no
    equivalent there is overstating what it did.
    """

    document_id: str
    base_jurisdiction: str | None = None
    target_jurisdiction: str
    delta: list[RiskDelta] = Field(default_factory=list)
    unmapped_rules: list[str] = Field(default_factory=list)
    evaluated_rules: int = 0
