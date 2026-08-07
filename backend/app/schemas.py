"""Shared wire contracts.

`EscalationRecord` and `ReviewStatus` are written by three different people (Clause NER,
the Redline Generator, and the frontend on resolve). WORK-SPLIT.md pins the field names
and enum values and says the first person to touch them owns them — that's this file.

Dev 1 and the Redline Generator import from here. Do not re-declare these enums; a
mismatch between `pending_review` and `pending` splits the review queue silently.
Any change to `EscalationRecord` updates the block in WORK-SPLIT.md in the same PR.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class EscalationSource(StrEnum):
    CLAUSE_NER = "clause_ner"
    REDLINE_GENERATOR = "redline_generator"


class EscalationReason(StrEnum):
    AMBIGUOUS_EDGE = "ambiguous_edge"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LOW_CONFIDENCE = "low_confidence"


class TraceRound(BaseModel):
    """One round of a bounded loop.

    Both escalation sources fill this in: Clause NER puts the retry strategy it tried in
    `attempt`, the Redline Generator puts the query it issued. Dev 1 renders one
    component for both.
    """

    round: int
    attempt: str
    result: str
    resolved: bool


class EscalationRecord(BaseModel):
    """Field order matches the JSON block in WORK-SPLIT.md exactly."""

    status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    source: EscalationSource
    reason: EscalationReason
    document_id: str
    clause_ref: str
    rounds_attempted: int
    trace: list[TraceRound] = Field(default_factory=list)
    reviewer_id: str | None = None
    resolved_at: datetime | None = None


class EdgeType(StrEnum):
    OVERRIDES = "OVERRIDES"
    DEPENDS_ON = "DEPENDS_ON"
    WAIVES = "WAIVES"
    MODIFIES = "MODIFIES"


class FactType(StrEnum):
    LIABILITY_CAP = "liability_cap"
    JURISDICTION = "jurisdiction"
    DATE = "date"
    MONETARY_VALUE = "monetary_value"
    OBLIGATION = "obligation"
    PARTY = "party"


class Provenance(StrEnum):
    """How a fact or edge reached its current state.

    §4.2 requires that human-confirmed facts are never indistinguishable from
    AI-generated ones. This is that guarantee — and it also tells us how often the
    retry loop actually saves a human round-trip.
    """

    DIRECT_EXTRACTION = "direct_extraction"
    RETRY_KG_PRECEDENT = "retry:kg_precedent"
    RETRY_WIDEN_CONTEXT = "retry:widen_context"
    RETRY_ALTERNATE_PARSE = "retry:alternate_parse"
    HUMAN = "human"


class Chunk(BaseModel):
    """Dev 2's output — the input contract for this stage.

    Proposed shape, published as `fixtures/chunks.sample.json`. `embedding` is optional
    because nothing in this stage reads it: the widen_context retry strategy walks
    character spans rather than vector similarity, which keeps us independent of Dev 2's
    embedding model choice.
    """

    chunk_id: str
    document_id: str
    text: str
    page: int
    char_start: int
    char_end: int
    ocr_confidence: float
    clause_ref: str
    section_type: str = "clause"  # "clause" | "definitions" | "preamble"
    embedding: list[float] | None = None


class Fact(BaseModel):
    fact_id: str
    document_id: str
    clause_ref: str
    fact_type: FactType
    value: dict
    confidence: float
    source_chunk_ids: list[str]
    provenance: Provenance = Provenance.DIRECT_EXTRACTION


class KGEdge(BaseModel):
    edge_id: str
    document_id: str
    src_clause_ref: str
    dst_clause_ref: str
    edge_type: EdgeType
    status: ReviewStatus
    confidence: float
    evidence_chunk_ids: list[str]
    # The drafting pattern the extractor matched, e.g. "notwithstanding_shall_control".
    # This is what makes precedent lookup a plain SQL query: "has this same relationship
    # shape already been confirmed elsewhere in the document?"
    pattern_key: str | None = None
    resolved_by: Provenance | None = None


class CandidateEdge(BaseModel):
    """An edge the extractor could not commit to a single `edge_type`.

    Never written straight to `pending_review` — it goes through the bounded retry loop
    in `app.ambiguity` first.
    """

    edge_id: str
    document_id: str
    src_clause_ref: str
    dst_clause_ref: str
    candidate_types: list[EdgeType]
    confidence: float
    evidence_chunk_ids: list[str]
    pattern_key: str | None = None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidate_types) > 1
