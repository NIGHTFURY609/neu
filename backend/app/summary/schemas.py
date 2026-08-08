"""The summary wire contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import RiskSeverity


class CitationKind(StrEnum):
    CLAUSE = "clause"
    FACT = "fact"
    RISK = "risk"
    REDLINE = "redline"
    EDGE = "edge"


class Citation(BaseModel):
    kind: CitationKind
    # clause_ref | fact_id | risk_id | redline_id | edge_id
    ref: str


class Claim(BaseModel):
    """A statement plus the rows it is grounded in.

    `min_length=1` on sources is the point of the whole model. A claim with no citation
    is not a summary of this contract — it is a sentence about contracts in general, and
    there is no way for a reviewer to check it.
    """

    text: str
    sources: list[Citation] = Field(min_length=1)


class RiskHighlight(BaseModel):
    risk_id: str
    clause_ref: str
    severity: RiskSeverity
    rule_id: str
    rule_version: int
    statement: Claim


class ContractSummary(BaseModel):
    document_id: str
    generated_at: datetime
    provider: Literal["mock", "codex"]
    # sha256 over the ids that fed the prompt. Changes when any input row changes, which
    # is how staleness is detected without diffing the content.
    input_fingerprint: str

    parties: list[Claim] = Field(default_factory=list)
    term: Claim | None = None
    key_obligations: list[Claim] = Field(default_factory=list)
    payment: Claim | None = None
    liability_cap: Claim | None = None
    termination: Claim | None = None
    governing_law: Claim | None = None
    top_risks: list[RiskHighlight] = Field(default_factory=list)
    unusual_clauses: list[Claim] = Field(default_factory=list)

    # Not claims — provenance about the summary itself. "3 chunks carry no clause number
    # and are not represented below", "lowest page OCR confidence 0.64". This is the
    # channel for what the summary could not see, which a summary otherwise silently
    # omits.
    coverage_notes: list[str] = Field(default_factory=list)


class SummaryResponse(BaseModel):
    summary: ContractSummary
    cached: bool = False
    # True when the stored summary was generated from a different set of input rows than
    # the document currently has — e.g. a reviewer resolved an escalation since.
    stale: bool = False
