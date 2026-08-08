"""GET /compare and GET /documents/{id}/risk-preview."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models
from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.compare.schemas import ComparisonResult, RiskDelta, RiskPreview
from app.compare.service import compare_documents
from app.db import get_session
from app.kg import store as kg_store
from app.playbook_store import load_active_playbook
from app.risk import pipeline as risk_pipeline
from app.risk import store as risk_store

router = APIRouter(tags=["compare"])


def _visible_or_404(session: Session, document_id: str, principal: Principal) -> models.Document:
    """404 for both "absent" and "not yours".

    Unlike the document routes, which use 403 so a reviewer following a link is told why
    they cannot see something, both ids here are typed in by the caller. Distinguishing
    the two responses would turn this endpoint into an oracle for which document ids
    exist.
    """
    document = session.get(models.Document, document_id)
    if document is None or document.owner_id != principal.user_id:
        raise HTTPException(404, f"document {document_id} not found")
    return document


@router.get("/compare", response_model=ComparisonResult)
def compare(
    left: str = Query(..., description="Baseline document id."),
    right: str = Query(..., description="Document to compare against the baseline."),
    diff_detail: Literal["none", "changed_only", "all"] = Query(default="changed_only"),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> ComparisonResult:
    """Compare two documents.

    GET rather than POST: this creates no server state, so the URL is shareable into a
    review thread and cacheable by the browser. `pairing` in the response says whether
    the two turned out to be versions of one contract or unrelated documents — the caller
    does not have to know which it is asking for.

    `diff_detail` defaults to `changed_only` because word-diffing clauses that are
    identical is pure waste on both sides of the wire.
    """
    if left == right:
        raise HTTPException(400, "left and right must be different documents")
    left_row = _visible_or_404(session, left, principal)
    right_row = _visible_or_404(session, right, principal)
    return compare_documents(session, left_row, right_row, diff_detail=diff_detail)


@router.get("/documents/{document_id}/risk-preview", response_model=RiskPreview)
def risk_preview(
    document_id: str,
    jurisdiction: str = Query(..., description="Jurisdiction whose playbook to apply."),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> RiskPreview:
    """How would this contract fare under another jurisdiction's playbook?

    Re-runs the risk engine over the document's existing facts and confirmed edges with a
    different rule set, and returns the difference as `RiskDelta` — the same shape
    `/compare` returns, so the UI renders one component for both.

    Writes nothing. That is why it is a GET: re-running it during a demo is free, and the
    stored risk flags stay the ones that were actually evaluated for this document.

    `unmapped_rules` names the rules that fired on the stored assessment but have no
    counterpart in the target playbook. Without it the response would imply full coverage
    of a foreign regime, which is exactly the overclaim a legal tool cannot afford.
    """
    document = _visible_or_404(session, document_id, principal)

    target_rules, _ = load_active_playbook(session, jurisdiction)
    if not target_rules:
        raise HTTPException(404, f"no active playbook rules for jurisdiction {jurisdiction}")

    facts = kg_store.list_facts(session, document_id)
    edges = kg_store.list_edges(session, document_id)  # confirmed only, by default
    records = risk_pipeline.run_risk_assessment(document_id, target_rules, facts, edges)
    target_flags = risk_pipeline.to_risk_flags(document_id, records, target_rules, facts)

    current_flags = risk_store.list_risk_flags(session, document_id)
    # Same document, so clause refs are stable and map to themselves. The alignment step
    # that `/compare` needs is unnecessary here.
    identity = {flag.clause_ref: flag.clause_ref for flag in current_flags}
    identity.update({flag.clause_ref: flag.clause_ref for flag in target_flags})

    from app.compare.service import _risk_deltas

    delta: list[RiskDelta] = _risk_deltas(current_flags, target_flags, identity)

    target_rule_ids = {rule.rule_id for rule in target_rules}
    unmapped = sorted({f.rule_id for f in current_flags} - target_rule_ids)

    return RiskPreview(
        document_id=document_id,
        base_jurisdiction=document.jurisdiction,
        target_jurisdiction=jurisdiction,
        delta=delta,
        unmapped_rules=unmapped,
        evaluated_rules=len(target_rules),
    )
