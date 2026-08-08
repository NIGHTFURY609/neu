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


class EscalationItem(EscalationRecord):
    """An escalation as the Review Queue serves it.

    `EscalationRecord` is what a stage *writes*. Reading one back also needs the row's
    own id, and the thing a resolution has to write back to: a candidate KG edge for
    Clause NER escalations, a redline for the Redline Generator's low_confidence path.
    Exactly one of the two targets is set, or neither (budget_exhausted writes no
    redline). `pipeline.publish_fixtures()` emits these extra fields, so
    `fixtures/escalation.sample.json` and `fixtures/escalation.redline.json` are this
    model's golden samples.

    Additive on purpose: `EscalationRecord` is untouched, so the JSON block in
    WORK-SPLIT.md still describes the written record verbatim.
    """

    id: str
    # None for escalations that aren't about a KG edge — the Redline Generator's
    # budget_exhausted / low_confidence paths.
    target_edge_id: str | None = None
    # None for everything except the Redline Generator's low_confidence path, where a
    # redline exists but must not be served until a human approves it.
    target_redline_id: str | None = None


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


# ------------------------------------------------------------------- Risk Engine input
#
# Dev 4's output, and the Redline Generator's input. Shapes follow the `risk_flags` and
# `playbook_rules` tables in the repo-root `schema.sql`, which is Dev 4's own file —
# published here as Pydantic so this stage can be built and tested before Dev 4 lands.
#
# One mismatch to settle at integration: `schema.sql` types `document_id` as `uuid`,
# while every Dev 2/Dev 3 model and fixture uses text (`"DOC-001"`). Text wins here so
# the stage runs against Dev 3's real output; the two have to converge before either
# side talks to the same database.


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskFlagStatus(StrEnum):
    """`schema.sql risk_flags.status`.

    `suppressed` means a confirmed KG edge waives or overrides the violation, so the
    Risk Engine already decided it is not a finding. Only `flagged` triggers a redline.
    """

    FLAGGED = "flagged"
    SUPPRESSED = "suppressed"


class RiskFlag(BaseModel):
    id: str
    document_id: str
    clause_ref: str
    rule_id: str
    # Snapshot at evaluation time — the rule may have been superseded since, and the
    # redline has to be justified against the version that actually fired.
    rule_version: int
    severity: RiskSeverity
    status: RiskFlagStatus
    suppressing_edge_id: str | None = None
    triggering_fact_ids: list[str] = Field(default_factory=list)


class PlaybookRule(BaseModel):
    """`schema.sql playbook_rules`, joined to a flag on (rule_id, version).

    `standard_language` is the compliant wording the generator rewrites *toward*, and
    `rationale` is the supporting legal rationale §6 requires. Both come from the
    playbook rather than the model, which is what keeps a generated redline traceable
    to a versioned rule instead of to an LLM's opinion.
    """

    rule_id: str
    version: int
    is_active: bool = True
    clause_pattern: str
    conditions: list[dict] = Field(default_factory=list)
    severity: RiskSeverity
    rationale: str
    allowed_overrides: list[EdgeType] = Field(default_factory=list)
    standard_language: str | None = None


# ------------------------------------------------------------------ Redline Generator


class Redline(BaseModel):
    """§3.4 output. Never served alone — always with its rationale, confidence and trace.

    `status` is `confirmed` when the loop reached sufficient grounding *and* cleared the
    confidence threshold, `pending_review` when it is waiting on human approval. A
    redline the loop could not ground is not written at all (§4.1 exit 2), so there is
    no `rejected` state here that the generator itself produces.
    """

    redline_id: str
    document_id: str
    risk_id: str
    clause_ref: str
    status: ReviewStatus
    confidence: float
    original_text: str
    suggested_text: str
    rationale: str
    rounds_attempted: int
    # The §4.1 retrieval trace — same shape as Clause NER's retry trace, so Dev 1
    # renders one component for both.
    trace: list[TraceRound] = Field(default_factory=list)
    grounding_chunk_ids: list[str] = Field(default_factory=list)
    grounding_edge_ids: list[str] = Field(default_factory=list)
