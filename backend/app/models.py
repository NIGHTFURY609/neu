"""Metadata DB schema.

Nobody had written this yet and Clause NER needs `documents` + `chunks` to exist before
it can read anything, so this stage owns the initial migration for all five tables.
Ownership of the *rows* still follows WORK-SPLIT.md:

  documents, chunks   -> written by Dev 2 (Ingestion)
  facts, kg_edges     -> written by Dev 3 (this stage)
  escalations         -> written by Dev 3 and the Redline Generator, resolved by Dev 1

Extend with new Alembic revisions rather than editing 0001.
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB}


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rbac_tags: Mapped[dict] = mapped_column(JSONB, default=dict)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = ["Base", "Document", "Chunk", "Fact", "KGEdge", "Escalation"]
