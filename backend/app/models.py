"""Metadata DB schema.

Nobody had written this yet and Clause NER needs `documents` + `chunks` to exist before
it can read anything, so this stage owns the initial migration for all six tables.
Ownership of the *rows* still follows WORK-SPLIT.md:

  documents, chunks   -> written by Dev 2 (Ingestion)
  facts, kg_edges     -> written by Dev 3 (this stage)
  redlines            -> written by the Redline Generator (Dev 3, revision 0002)
  escalations         -> written by Dev 3 and the Redline Generator, resolved by Dev 1

Extend with new Alembic revisions rather than editing 0001.
"""

import uuid as uuid_module
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings

# Kept identical to alembic/versions/0005_search_index.py. The two-argument form is
# required: to_tsvector(text) is only STABLE, and Postgres rejects it in a generated
# column.
_CHUNK_TSV = (
    "setweight(to_tsvector('english', coalesce(clause_ref, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(text, '')), 'B')"
)
# Kept identical to alembic/versions/0006_regulatory_provisions.py.
_PROVISION_TSV = (
    "setweight(to_tsvector('english', coalesce(citation, '') || ' ' || coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(summary, '')), 'B') || "
    "setweight(to_tsvector('english', coalesce(body, '')), 'C')"
)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB}


class Document(Base):
    """Revision 0004 added classification, version lineage and processing job state.

    Lineage carries both `contract_family` and `parent_document_id` on purpose: the
    parent alone needs a recursive CTE to fetch a family, and the family alone loses
    branch structure. Together, "every version of this contract, in order" is one
    indexed query.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("doc_kind IN ('contract', 'regulation')", name="ck_documents_doc_kind"),
        CheckConstraint("version >= 1", name="ck_documents_version_positive"),
        CheckConstraint(
            "processing_status IN ('pending', 'queued', 'running', 'succeeded', 'failed')",
            name="ck_documents_processing_status",
        ),
        UniqueConstraint("contract_family", "version", name="uq_documents_family_version"),
    )

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    # `filename` is an upload artifact — RawFileStore stores everything as `original.<ext>`
    # under a uuid4 directory, so a picker over six documents all named "original.txt" is
    # unusable. This is what a human actually calls the thing.
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # JSON array of strings, e.g. ["legal-team", "confidentiality:internal"]. Was an
    # object ({tag: true} from ingestion, {key: value} from app.pipeline) until 0004
    # migrated both shapes; `app.auth.tags.normalize_tags` still accepts either.
    rbac_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    doc_kind: Mapped[str] = mapped_column(String, nullable=False, default="contract")
    jurisdiction: Mapped[str | None] = mapped_column(String, nullable=True)
    contract_family: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.document_id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Job state lives here rather than in a `processing_jobs` table: it is 1:1 with a
    # document and the UI polls per document, so status is a primary-key read with no
    # join. A separate table would only earn its keep with retry history or concurrent
    # jobs per document, neither of which exists.
    processing_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", index=True
    )
    processing_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class Chunk(Base):
    """Dev 2's output. Read-only from this stage's point of view."""

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # §7 names bad OCR as a silent failure that degrades every downstream stage.
    # Dev 2 tracks it per page; we surface it on every fact we derive from a chunk.
    ocr_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    clause_ref: Mapped[str] = mapped_column(String, index=True)
    section_type: Mapped[str] = mapped_column(String, default="clause")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embed_dim), nullable=True)
    # Revision 0005. Generated and GIN-indexed; `clause_ref` outranks the body so that
    # searching "2.2" finds clause 2.2 rather than the clauses citing it. SQLAlchemy
    # omits Computed columns from INSERT/UPDATE, so `postgres_sync._write`'s
    # `session.merge(Chunk(...))` keeps working untouched.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_CHUNK_TSV, persisted=True), nullable=True
    )


class Fact(Base):
    __tablename__ = "facts"

    fact_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    clause_ref: Mapped[str] = mapped_column(String, index=True)
    fact_type: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    provenance: Mapped[str] = mapped_column(String, default="direct_extraction")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KGEdge(Base):
    """The Knowledge Graph, stored relationally.

    Typed structural relationships between clauses. The Risk Engine reads
    `status == 'confirmed'` only — `pending_review` edges are treated as absent.
    """

    __tablename__ = "kg_edges"

    edge_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    src_clause_ref: Mapped[str] = mapped_column(String, index=True)
    dst_clause_ref: Mapped[str] = mapped_column(String, index=True)
    edge_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # Drafting pattern matched by the extractor. Indexed because the kg_precedent retry
    # strategy queries it: "same shape, already confirmed, same document?"
    pattern_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # How this edge reached its status: direct extraction, which retry strategy
    # resolved it, or a human. §4.2 requires human-confirmed and AI-generated facts
    # stay distinguishable forever.
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Redline(Base):
    """A suggested rewrite of one clause, and the evidence it was written from.

    Added in revision 0002. `status` carries the same meaning as on `kg_edges`, and for
    the same reason: `redline.store.list_redlines` defaults to confirmed, so a
    `pending_review` redline exists in the database but is not served to anyone until a
    reviewer approves it.

    The grounding columns are not decoration. §3.4 makes this the highest
    hallucination-risk stage in the system, so every redline has to name the chunks and
    confirmed edges it was written from, and `trace` records the retrieval rounds that
    found them.
    """

    __tablename__ = "redlines"

    redline_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    # The Risk Engine flag that triggered this. `risk_flags` now exists — revision 0003
    # re-creates Dev 4's tables under Alembic — and the document_id mismatch was settled
    # in favour of text (see WORK-SPLIT.md). Still no FK: a redline may be generated from
    # a flag held in memory, so requiring the row to exist first would couple Stage 4's
    # in-memory runs to the database.
    risk_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    clause_ref: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    rounds_attempted: Mapped[int] = mapped_column(Integer, default=0)
    trace: Mapped[list] = mapped_column(JSONB, default=list)
    grounding_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    grounding_edge_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Escalation(Base):
    """One row per item in the Human Review Queue.

    Column set mirrors `app.schemas.EscalationRecord` — see WORK-SPLIT.md. Dev 1 writes
    `status`, `reviewer_id` and `resolved_at` on resolve; everything else is written once
    by whichever stage escalated.
    """

    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String, index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    clause_ref: Mapped[str] = mapped_column(String, nullable=False)
    rounds_attempted: Mapped[int] = mapped_column(Integer, default=0)
    # The retry trace (Clause NER) or retrieval trace (Redline Generator). Same shape
    # for both — Dev 1 renders one component.
    trace: Mapped[list] = mapped_column(JSONB, default=list)
    reviewer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_edge_id: Mapped[str | None] = mapped_column(
        ForeignKey("kg_edges.edge_id"), nullable=True, index=True
    )
    # Set only by the Redline Generator's low-confidence path. Resolving the escalation
    # flips this redline's status, the same way target_edge_id flips an edge — without
    # it, approving the item in the queue would change nothing anyone downstream sees.
    target_redline_id: Mapped[str | None] = mapped_column(
        ForeignKey("redlines.redline_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlaybookRule(Base):
    """A versioned compliance rule. Revision 0003 (Dev 4's tables, re-created under
    Alembic). Only one version per `rule_id` may be `is_active` at a time — see the
    partial unique index in that migration — but superseded versions stay in the table
    forever, because a `RiskFlag` stays justifiable against the version that fired it.
    """

    __tablename__ = "playbook_rules"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')", name="ck_playbook_rules_severity"
        ),
        UniqueConstraint("rule_id", "version", name="uq_playbook_rules_rule_id_version"),
    )

    id: Mapped[uuid_module.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    clause_pattern: Mapped[str] = mapped_column(String, nullable=False)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_overrides: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    standard_language: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL means "applies in every jurisdiction", so revision 0004 needed no backfill
    # decision for the rules that already existed. Filtering happens in
    # `playbook_store.load_active_playbook`, deliberately not on `risk.rules.Rule` —
    # that dataclass validates against a closed KNOWN_RULE_FIELDS set pinned by a large
    # test surface, and the engine is already pure over whatever rule list it is handed.
    jurisdiction: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskFlag(Base):
    """The Risk Engine's output (§3.3), keyed to the specific clause/fact/rule version
    that triggered it. Revision 0003. `id` is `String`, not uuid — `redline/generator.py`
    derives a redline id with `f"RL-{risk.id.removeprefix('RISK-')}"`, a string op.
    """

    __tablename__ = "risk_flags"
    __table_args__ = (
        CheckConstraint("status IN ('flagged', 'suppressed')", name="ck_risk_flags_status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    clause_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Nullable: only a `suppressed` flag names the confirmed edge that waived it.
    suppressing_edge_id: Mapped[str | None] = mapped_column(
        ForeignKey("kg_edges.edge_id"), nullable=True
    )
    triggering_fact_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "Base",
    "Document",
    "Chunk",
    "Fact",
    "KGEdge",
    "Redline",
    "Escalation",
    "PlaybookRule",
    "RiskFlag",
    "RegulatoryProvision",
    "Summary",
    "NegotiationPosition",
]


class RegulatoryProvision(Base):
    """One provision of published law. Revision 0006.

    Deliberately has no `rbac_tags` and is deliberately never filtered by
    `visible_documents`: statutes are public, and access-controlling GDPR would be a
    category error. If you find yourself adding tags here, the thing you actually want is
    a regulation *uploaded* through `/ingest/upload` with `doc_kind='regulation'`, which
    is a normal Document and is access-controlled like one.
    """

    __tablename__ = "regulatory_provisions"

    provision_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument: Mapped[str] = mapped_column(String, nullable=False)
    instrument_title: Mapped[str] = mapped_column(String, nullable=False)
    citation: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String, nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    topic_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    clause_patterns: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    rule_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    retrieved_at: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_PROVISION_TSV, persisted=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Summary(Base):
    """A cached contract summary. Revision 0007.

    One row per document: regenerating replaces. `input_fingerprint` is a hash over the
    ids that fed the prompt, so a resolved escalation or a new risk flag makes the stored
    summary detectably stale without anyone having to diff its prose.
    """

    __tablename__ = "summaries"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), primary_key=True
    )
    input_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NegotiationPosition(Base):
    """One rung of a fallback ladder. Revision 0008.

    Three rungs per flagged clause at most — preferred, acceptable, walk_away — each
    traceable to the same versioned playbook rule the redline was written against. A rule
    with no `allowed_overrides` yields no acceptable tier, which is the honest answer:
    the playbook permits no concession there.
    """

    __tablename__ = "negotiation_positions"
    __table_args__ = (
        CheckConstraint(
            "tier IN ('preferred', 'acceptable', 'walk_away')", name="ck_negotiation_tier"
        ),
        CheckConstraint(
            "residual_severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_negotiation_residual_severity",
        ),
        UniqueConstraint("redline_id", "tier", name="uq_negotiation_redline_tier"),
    )

    position_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), index=True
    )
    redline_id: Mapped[str] = mapped_column(
        ForeignKey("redlines.redline_id", ondelete="CASCADE"), index=True
    )
    risk_id: Mapped[str] = mapped_column(String, nullable=False)
    clause_ref: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    concession: Mapped[str] = mapped_column(Text, nullable=False)
    residual_severity: Mapped[str] = mapped_column(String, nullable=False)
    grounded_in_override: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
