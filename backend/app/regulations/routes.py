"""Regulation lookup routes.

Note the absence of RBAC on the provision reads. Published law is public; gating GDPR
behind a tag would be a category error. Access control still applies to the *document*
whose risk flag or clause is being asked about — you cannot use these routes to learn
what rules a document you cannot see triggered.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document
from app.compare.assemble import assemble_clauses
from app.db import get_session
from app.kg import store as kg_store
from app.regulations.matcher import (
    RegulationMatch,
    RegulatoryProvisionOut,
    lexical_matches,
    match_risk_flag,
)

router = APIRouter(tags=["regulations"])


@router.get("/risk-flags/{risk_id}/regulations", response_model=list[RegulationMatch])
def regulations_for_risk(
    risk_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RegulationMatch]:
    """The law a specific finding implicates.

    Authored links first — those are citations. Lexical matches follow and are labelled
    as such, so a reviewer can tell "someone decided this statute is relevant" from "these
    words overlap".
    """
    flag = session.get(models.RiskFlag, risk_id)
    if flag is None:
        raise HTTPException(404, f"no risk flag {risk_id}")
    authorize_document(session, flag.document_id, principal)

    rule = session.scalar(
        select(models.PlaybookRule).where(
            models.PlaybookRule.rule_id == flag.rule_id,
            models.PlaybookRule.version == flag.rule_version,
        )
    )
    return match_risk_flag(session, flag, rule)


@router.get(
    "/documents/{document_id}/clauses/{clause_ref}/regulations",
    response_model=list[RegulationMatch],
)
def regulations_for_clause(
    document_id: str,
    clause_ref: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RegulationMatch]:
    """The law a clause implicates, with no rule to anchor it.

    Weaker than the risk-flag route by construction: with no fired rule there is no
    authored link and no rationale, so every match here is lexical over the clause text.
    Every result is labelled `lexical` accordingly.
    """
    document = session.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, f"document {document_id} not found")
    authorize_document(session, document_id, principal)

    clauses = assemble_clauses(kg_store.list_chunks(session, document_id))
    clause = clauses.get(clause_ref)
    if clause is None:
        raise HTTPException(404, f"no clause {clause_ref} in {document_id}")

    return lexical_matches(
        session,
        query_text=clause.text[:1500],
        jurisdiction=document.jurisdiction,
    )


@router.get("/regulations", response_model=list[RegulatoryProvisionOut])
def list_regulations(
    jurisdiction: str | None = Query(default=None),
    instrument: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[RegulatoryProvisionOut]:
    stmt = select(models.RegulatoryProvision)
    if jurisdiction:
        stmt = stmt.where(models.RegulatoryProvision.jurisdiction == jurisdiction)
    if instrument:
        stmt = stmt.where(models.RegulatoryProvision.instrument == instrument)
    stmt = stmt.order_by(
        models.RegulatoryProvision.instrument, models.RegulatoryProvision.provision_id
    )
    return [_out(row) for row in session.scalars(stmt)]


@router.get("/regulations/{provision_id}", response_model=RegulatoryProvisionOut)
def get_regulation(
    provision_id: str, session: Session = Depends(get_session)
) -> RegulatoryProvisionOut:
    row = session.get(models.RegulatoryProvision, provision_id)
    if row is None:
        raise HTTPException(404, f"no provision {provision_id}")
    return _out(row)


def _out(row: models.RegulatoryProvision) -> RegulatoryProvisionOut:
    return RegulatoryProvisionOut(
        provision_id=row.provision_id,
        citation=row.citation,
        title=row.title,
        instrument=row.instrument,
        instrument_title=row.instrument_title,
        jurisdiction=row.jurisdiction,
        summary=row.summary,
        body=row.body,
        topic_tags=list(row.topic_tags or []),
        rule_ids=list(row.rule_ids or []),
        source_url=row.source_url,
        retrieved_at=row.retrieved_at.isoformat() if row.retrieved_at else None,
        notes=row.notes,
    )
