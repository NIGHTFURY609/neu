"""Persistence for facts, KG edges and escalations.

The one rule worth stating out loud: `list_edges` defaults to confirmed only. The Risk
Engine treats `pending_review` edges as absent until resolved, and enforcing that here
means no downstream reader has to remember to.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import EscalationRecord, Fact, KGEdge, ReviewStatus


def _edge_to_schema(row: models.KGEdge) -> KGEdge:
    return KGEdge.model_validate(
        {
            "edge_id": row.edge_id,
            "document_id": row.document_id,
            "src_clause_ref": row.src_clause_ref,
            "dst_clause_ref": row.dst_clause_ref,
            "edge_type": row.edge_type,
            "status": row.status,
            "confidence": row.confidence,
            "evidence_chunk_ids": list(row.evidence_chunk_ids or []),
            "pattern_key": row.pattern_key,
            "resolved_by": row.resolved_by,
        }
    )


def _fact_to_schema(row: models.Fact) -> Fact:
    return Fact.model_validate(
        {
            "fact_id": row.fact_id,
            "document_id": row.document_id,
            "clause_ref": row.clause_ref,
            "fact_type": row.fact_type,
            "value": row.value,
            "confidence": row.confidence,
            "source_chunk_ids": list(row.source_chunk_ids or []),
            "provenance": row.provenance,
        }
    )


def list_edges(
    session: Session,
    document_id: str,
    status: ReviewStatus | None = ReviewStatus.CONFIRMED,
) -> list[KGEdge]:
    """Edges for a document. Pass `status=None` to see every status."""
    stmt = select(models.KGEdge).where(models.KGEdge.document_id == document_id)
    if status is not None:
        stmt = stmt.where(models.KGEdge.status == status.value)
    return [_edge_to_schema(row) for row in session.scalars(stmt)]


def list_facts(session: Session, document_id: str) -> list[Fact]:
    stmt = select(models.Fact).where(models.Fact.document_id == document_id)
    return [_fact_to_schema(row) for row in session.scalars(stmt)]


def save_facts(session: Session, facts: list[Fact]) -> None:
    for fact in facts:
        session.merge(
            models.Fact(
                fact_id=fact.fact_id,
                document_id=fact.document_id,
                clause_ref=fact.clause_ref,
                fact_type=fact.fact_type.value,
                value=fact.value,
                confidence=fact.confidence,
                source_chunk_ids=fact.source_chunk_ids,
                provenance=fact.provenance.value,
            )
        )


def save_edges(session: Session, edges: list[KGEdge]) -> None:
    for edge in edges:
        session.merge(
            models.KGEdge(
                edge_id=edge.edge_id,
                document_id=edge.document_id,
                src_clause_ref=edge.src_clause_ref,
                dst_clause_ref=edge.dst_clause_ref,
                edge_type=edge.edge_type.value,
                status=edge.status.value,
                confidence=edge.confidence,
                evidence_chunk_ids=edge.evidence_chunk_ids,
                pattern_key=edge.pattern_key,
                resolved_by=edge.resolved_by.value if edge.resolved_by else None,
            )
        )


def save_escalations(
    session: Session, escalations: list[tuple[str, str | None, EscalationRecord]]
) -> None:
    """Persist `(escalation_id, target_edge_id, record)` triples."""
    for escalation_id, target_edge_id, record in escalations:
        session.merge(
            models.Escalation(
                id=escalation_id,
                status=record.status.value,
                source=record.source.value,
                reason=record.reason.value,
                document_id=record.document_id,
                clause_ref=record.clause_ref,
                rounds_attempted=record.rounds_attempted,
                trace=[round_.model_dump() for round_ in record.trace],
                reviewer_id=record.reviewer_id,
                resolved_at=record.resolved_at,
                target_edge_id=target_edge_id,
            )
        )
