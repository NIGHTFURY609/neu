"""Persistence for risk flags.

Same shape as `app.redline.store`: this module only knows about `app.schemas.RiskFlag`,
not `app.risk.audit.AuditRecord` — the AuditRecord -> RiskFlag translation happens once,
in `app.risk.pipeline.to_risk_flags`, before anything gets here.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import RiskFlag, RiskFlagStatus


def _to_schema(row: models.RiskFlag) -> RiskFlag:
    return RiskFlag.model_validate(
        {
            "id": row.id,
            "document_id": row.document_id,
            "clause_ref": row.clause_ref,
            "rule_id": row.rule_id,
            "rule_version": row.rule_version,
            "severity": row.severity,
            "status": row.status,
            "suppressing_edge_id": row.suppressing_edge_id,
            "triggering_fact_ids": list(row.triggering_fact_ids or []),
        }
    )


def save_risk_flags(session: Session, flags: list[RiskFlag]) -> None:
    for flag in flags:
        session.merge(
            models.RiskFlag(
                id=flag.id,
                document_id=flag.document_id,
                clause_ref=flag.clause_ref,
                rule_id=flag.rule_id,
                rule_version=flag.rule_version,
                severity=flag.severity.value,
                status=flag.status.value,
                suppressing_edge_id=flag.suppressing_edge_id,
                triggering_fact_ids=flag.triggering_fact_ids,
            )
        )


def list_risk_flags(
    session: Session,
    document_id: str,
    status: RiskFlagStatus | None = None,
) -> list[RiskFlag]:
    """Risk flags for a document. `status=None` (the default) returns every status — the
    dashboard shows suppressed flags alongside open ones, unlike the confirmed-only
    default on `kg.store.list_edges`/`redline.store.list_redlines`."""
    stmt = select(models.RiskFlag).where(models.RiskFlag.document_id == document_id)
    if status is not None:
        stmt = stmt.where(models.RiskFlag.status == status.value)
    stmt = stmt.order_by(models.RiskFlag.id)
    return [_to_schema(row) for row in session.scalars(stmt)]
