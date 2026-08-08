"""Persistence for redlines.

Same rule as `kg/store.list_edges`, one stage further down: `list_redlines` defaults to
confirmed only. A `pending_review` redline is one the loop grounded but could not stand
behind, and §4.1 says it must not be served until a human approves it. Enforcing that in
the read means no route, dashboard or export has to remember to.

Escalations still go through `kg.store.save_escalations` — one queue, one writer, per
§4.2. This module only adds the `target_redline_id` link.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import EscalationRecord, Redline, ReviewStatus, TraceRound


def _to_schema(row: models.Redline) -> Redline:
    return Redline.model_validate(
        {
            "redline_id": row.redline_id,
            "document_id": row.document_id,
            "risk_id": row.risk_id,
            "clause_ref": row.clause_ref,
            "status": row.status,
            "confidence": row.confidence,
            "original_text": row.original_text,
            "suggested_text": row.suggested_text,
            "rationale": row.rationale,
            "rounds_attempted": row.rounds_attempted,
            "trace": [TraceRound.model_validate(r) for r in row.trace or []],
            "grounding_chunk_ids": list(row.grounding_chunk_ids or []),
            "grounding_edge_ids": list(row.grounding_edge_ids or []),
        }
    )


def save_redlines(session: Session, redlines: list[Redline]) -> None:
    for redline in redlines:
        session.merge(
            models.Redline(
                redline_id=redline.redline_id,
                document_id=redline.document_id,
                risk_id=redline.risk_id,
                clause_ref=redline.clause_ref,
                status=redline.status.value,
                confidence=redline.confidence,
                original_text=redline.original_text,
                suggested_text=redline.suggested_text,
                rationale=redline.rationale,
                rounds_attempted=redline.rounds_attempted,
                trace=[round_.model_dump() for round_ in redline.trace],
                grounding_chunk_ids=redline.grounding_chunk_ids,
                grounding_edge_ids=redline.grounding_edge_ids,
            )
        )


def list_redlines(
    session: Session,
    document_id: str,
    status: ReviewStatus | None = ReviewStatus.CONFIRMED,
) -> list[Redline]:
    """Redlines for a document. Pass `status=None` to see every status."""
    stmt = select(models.Redline).where(models.Redline.document_id == document_id)
    if status is not None:
        stmt = stmt.where(models.Redline.status == status.value)
    stmt = stmt.order_by(models.Redline.redline_id)
    return [_to_schema(row) for row in session.scalars(stmt)]


def get_redline(session: Session, redline_id: str) -> Redline | None:
    row = session.get(models.Redline, redline_id)
    return _to_schema(row) if row is not None else None


def save_redline_escalations(
    session: Session, escalations: list[tuple[str, str | None, EscalationRecord]]
) -> None:
    """Persist `(escalation_id, target_redline_id, record)` triples.

    The mirror of `kg.store.save_escalations`, differing only in which target column it
    fills. `target_redline_id` is None on the budget-exhausted path — there is no redline
    to point at, and §4.1 is explicit that none may be written.
    """
    for escalation_id, target_redline_id, record in escalations:
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
                target_redline_id=target_redline_id,
            )
        )
