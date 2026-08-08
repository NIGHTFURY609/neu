"""Persistence for facts, KG edges and escalations.

The one rule worth stating out loud: `list_edges` defaults to confirmed only. The Risk
Engine treats `pending_review` edges as absent until resolved, and enforcing that here
means no downstream reader has to remember to.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import (
    EdgeType,
    EscalationItem,
    EscalationRecord,
    EscalationSource,
    Fact,
    KGEdge,
    Provenance,
    ReviewStatus,
)


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


def _escalation_to_schema(row: models.Escalation) -> EscalationItem:
    return EscalationItem.model_validate(
        {
            "id": row.id,
            "target_edge_id": row.target_edge_id,
            "target_redline_id": row.target_redline_id,
            "status": row.status,
            "source": row.source,
            "reason": row.reason,
            "document_id": row.document_id,
            "clause_ref": row.clause_ref,
            "rounds_attempted": row.rounds_attempted,
            "trace": list(row.trace or []),
            "reviewer_id": row.reviewer_id,
            "resolved_at": row.resolved_at,
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


def list_escalations(
    session: Session,
    *,
    statuses: list[ReviewStatus] | None = None,
    document_id: str | None = None,
    source: EscalationSource | None = None,
) -> list[EscalationItem]:
    """The Review Queue read. `statuses=None` means every status.

    Ordered oldest first so the queue is a queue — the item that has been waiting
    longest is the one a reviewer sees at the top.
    """
    stmt = select(models.Escalation)
    if statuses:
        stmt = stmt.where(models.Escalation.status.in_([s.value for s in statuses]))
    if document_id is not None:
        stmt = stmt.where(models.Escalation.document_id == document_id)
    if source is not None:
        stmt = stmt.where(models.Escalation.source == source.value)
    stmt = stmt.order_by(models.Escalation.created_at, models.Escalation.id)
    return [_escalation_to_schema(row) for row in session.scalars(stmt)]


def get_escalation(session: Session, escalation_id: str) -> EscalationItem | None:
    row = session.get(models.Escalation, escalation_id)
    return _escalation_to_schema(row) if row is not None else None


def resolve_escalation(
    session: Session,
    escalation_id: str,
    *,
    status: ReviewStatus,
    reviewer_id: str,
    edge_type: EdgeType | None = None,
) -> EscalationItem:
    """Write a human decision back to the escalation *and* whatever it targets.

    The second write is the one that matters downstream. `list_edges` and
    `redline.store.list_redlines` both default to confirmed, so until the target itself
    flips, resolving an escalation changes nothing anyone downstream can see. An
    escalation targets an edge (Clause NER) or a redline (the Redline Generator's
    low-confidence path), never both. `resolved_by` becomes `human` — §4.2 requires
    human-confirmed and AI-generated facts stay distinguishable forever; a redline's
    equivalent provenance is the escalation's own `reviewer_id`.

    `edge_type` pins the correct reading when the edge was escalated precisely because
    the extractor could not choose between several. Omitted, the candidate type stands.
    It has no meaning for a redline target and is ignored there.

    Caller commits. Raises `LookupError` if there is no such escalation.
    """
    row = session.get(models.Escalation, escalation_id)
    if row is None:
        raise LookupError(escalation_id)

    row.status = status.value
    row.reviewer_id = reviewer_id
    row.resolved_at = datetime.now(UTC)

    if row.target_edge_id is not None:
        edge = session.get(models.KGEdge, row.target_edge_id)
        if edge is not None:
            edge.status = status.value
            edge.resolved_by = Provenance.HUMAN.value
            if edge_type is not None:
                edge.edge_type = edge_type.value

    if row.target_redline_id is not None:
        redline = session.get(models.Redline, row.target_redline_id)
        if redline is not None:
            redline.status = status.value

    session.flush()
    return _escalation_to_schema(row)
