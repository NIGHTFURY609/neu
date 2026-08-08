"""Negotiation ladder routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import models
from app.auth.deps import get_principal, require_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document
from app.db import get_session
from app.negotiation.generator import build_ladder
from app.negotiation.schemas import NegotiationLadder, NegotiationPosition
from app.redline import store as redline_store
from app.risk import store as risk_store
from app.schemas import PlaybookRule

router = APIRouter(tags=["negotiation"])

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _rule_for(session: Session, rule_id: str, version: int) -> PlaybookRule | None:
    row = session.scalar(
        select(models.PlaybookRule).where(
            models.PlaybookRule.rule_id == rule_id, models.PlaybookRule.version == version
        )
    )
    if row is None:
        return None
    return PlaybookRule.model_validate(
        {
            "rule_id": row.rule_id,
            "version": row.version,
            "is_active": row.is_active,
            "clause_pattern": row.clause_pattern,
            "conditions": row.conditions,
            "severity": row.severity,
            "rationale": row.rationale,
            "allowed_overrides": row.allowed_overrides or [],
            "standard_language": row.standard_language,
        }
    )


def _build_all(session: Session, document_id: str) -> list[NegotiationLadder]:
    redlines = redline_store.list_redlines(session, document_id)
    flags = {f.id: f for f in risk_store.list_risk_flags(session, document_id)}

    ladders: list[NegotiationLadder] = []
    for redline in redlines:
        risk = flags.get(redline.risk_id)
        if risk is None:
            continue
        rule = _rule_for(session, risk.rule_id, risk.rule_version)
        if rule is None:
            # The rule version that fired has been deleted. Skipping is right: a ladder
            # without its rule cannot state what a concession costs, which is the only
            # thing it is for.
            continue
        ladders.append(build_ladder(redline, risk, rule))

    ladders.sort(key=lambda l: (_SEVERITY_ORDER.get(l.severity.value, 9), l.clause_ref))
    return ladders


@router.get("/documents/{document_id}/negotiation", response_model=list[NegotiationLadder])
def get_ladders(
    document_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[NegotiationLadder]:
    """Stored ladders, or freshly derived ones if none have been persisted.

    Falling back to deriving means the negotiation view is never empty just because
    nobody pressed generate — the ladders are a pure function of rows that already exist.
    """
    authorize_document(session, document_id, principal)

    stored = list(
        session.scalars(
            select(models.NegotiationPosition)
            .where(models.NegotiationPosition.document_id == document_id)
            .order_by(models.NegotiationPosition.clause_ref, models.NegotiationPosition.rank)
        )
    )
    if not stored:
        return _build_all(session, document_id)

    by_redline: dict[str, list[models.NegotiationPosition]] = {}
    for row in stored:
        by_redline.setdefault(row.redline_id, []).append(row)

    flags = {f.id: f for f in risk_store.list_risk_flags(session, document_id)}
    redlines = {r.redline_id: r for r in redline_store.list_redlines(session, document_id)}

    ladders: list[NegotiationLadder] = []
    for redline_id, rows in by_redline.items():
        redline = redlines.get(redline_id)
        risk = flags.get(rows[0].risk_id)
        if redline is None or risk is None:
            continue
        ladders.append(
            NegotiationLadder(
                redline_id=redline_id,
                clause_ref=rows[0].clause_ref,
                risk_id=rows[0].risk_id,
                rule_id=risk.rule_id,
                rule_version=risk.rule_version,
                severity=risk.severity,
                original_text=redline.original_text,
                positions=[
                    NegotiationPosition.model_validate(
                        {c.name: getattr(row, c.name) for c in row.__table__.columns}
                    )
                    for row in sorted(rows, key=lambda r: r.rank)
                ],
                no_fallback_available=not any(r.tier == "acceptable" for r in rows),
            )
        )
    ladders.sort(key=lambda l: (_SEVERITY_ORDER.get(l.severity.value, 9), l.clause_ref))
    return ladders


@router.post("/documents/{document_id}/negotiation/generate")
def generate_ladders(
    document_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_principal),
) -> dict:
    """Regenerate and persist. Idempotent — replaces whatever was there.

    Deletes before inserting rather than merging: a rule whose `allowed_overrides` was
    emptied since the last run must lose its acceptable rung, and a merge would leave the
    stale one in place — a fallback position the playbook no longer sanctions.
    """
    authorize_document(session, document_id, principal)
    ladders = _build_all(session, document_id)

    session.execute(
        delete(models.NegotiationPosition).where(
            models.NegotiationPosition.document_id == document_id
        )
    )
    count = 0
    for ladder in ladders:
        for position in ladder.positions:
            session.add(
                models.NegotiationPosition(
                    **position.model_dump(exclude={"created_at"}, mode="python")
                )
            )
            count += 1
    session.commit()
    return {"document_id": document_id, "ladders": len(ladders), "positions": count}
