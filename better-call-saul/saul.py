"""API — the outward-facing HTTP layer for Dev 4's Risk & Rules Engine.

Named for the folder's own namesake: Saul is the one who deals with the outside world.

Serves the exact contract backend/app/dashboard_stubs.py already stubbed:
GET /documents/{document_id}/risk-flags, shaped like fixtures/risk.sample.json. That
stub's docstring says "delete this module when that stage publishes... swap the fixture
read for a real call and the frontend does not change" — this is that publish.

Not wired into backend/app/api.py from here. better-call-saul/ isn't on that project's
Python path (different dependency set entirely — no sqlalchemy/alembic/psycopg here), and
per team convention we don't edit Dev 3's files directly. Mounting `router` there is a
one-line change for whoever owns that integration: swap
`app.include_router(dashboard_stubs.router)`'s risk-flags portion for this router.

Still fixture-backed, not DB-backed — same "mock now, connect later" as every other
function in this stage. Runnable standalone: `uvicorn saul:app --reload`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from chuck import AuditRecord, EvaluationResult
from gus import load_facts_fixture, load_kg_edges_fixture, run_risk_assessment
from kim import load_playbook

REPO_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"  # Dev 3's shared fixtures
OWN_FIXTURES = Path(__file__).resolve().parent / "fixtures"  # this stage's own playbook


class RiskFlag(BaseModel):
    """Matches fixtures/risk.sample.json field-for-field — the contract
    dashboard_stubs.py promised the frontend was built against."""

    id: str
    document_id: str
    clause_ref: str
    rule_id: str
    rule_version: int
    severity: str
    status: str  # "flagged" | "suppressed"
    suppressing_edge_id: str | None
    triggering_fact_ids: list[str]


# Only these two evaluation results are risk flags at all — COMPLIANT means nothing to
# report, SYSTEM_ERROR means nothing was safely determined. Neither belongs in a list
# that's specifically "here are your risks."
_STATUS_BY_RESULT = {
    EvaluationResult.FLAGGED: "flagged",
    EvaluationResult.WAIVED: "suppressed",
}


def _to_risk_flag(record: AuditRecord) -> RiskFlag:
    # Guaranteed non-None: both FLAGGED and WAIVED inherit rule.severity (chuck.py).
    assert record.final_severity is not None
    suppressing_edge_id = record.kg_overrides_found[0]["edge_id"] if record.kg_overrides_found else None
    return RiskFlag(
        id=record.audit_id,
        document_id=record.document_id,
        clause_ref=record.clause_ref,
        rule_id=record.rule_triggered,
        rule_version=record.rule_version,
        severity=record.final_severity,
        status=_STATUS_BY_RESULT[record.evaluation_result],
        suppressing_edge_id=suppressing_edge_id,
        triggering_fact_ids=list(record.triggering_fact_ids),
    )


def get_risk_flags_for_document(document_id: str) -> list[RiskFlag]:
    """The actual logic, kept separate from the route so it's testable without spinning up
    an HTTP client."""
    rules = load_playbook(str(OWN_FIXTURES / "sample_playbook.json"))
    all_facts = load_facts_fixture(REPO_FIXTURES / "facts.sample.json")
    all_edges = load_kg_edges_fixture(REPO_FIXTURES / "kg_edges.confirmed.json")

    # run_risk_assessment hard-rejects any fact/edge from a different document (by
    # design — see gus.py/mike.py), so callers must pre-filter, not rely on it to ignore
    # rows that don't belong to the document being queried.
    facts = [f for f in all_facts if f.document_id == document_id]
    edges = [e for e in all_edges if e.document_id == document_id]

    # A document with zero facts of ANY type is indistinguishable, from here, from one
    # that was never ingested at all — confirmed by testing: without this check, a made-up
    # document_id got back a "critical: missing liability clause" flag, because an
    # is_missing rule can't tell "genuinely absent from a real document" apart from
    # "there is no document." A real document missing just liability_cap would still have
    # OTHER fact types present, so this check doesn't suppress that legitimate case.
    #
    # Deliberately keyed on facts alone, not "facts and edges" — edges describe
    # relationships BETWEEN clauses, which presupposes clauses were already extracted as
    # facts. An edge existing for a document with zero facts isn't evidence the document
    # was processed; it's an inconsistent state that shouldn't override the facts signal
    # (confirmed by testing: this exact combination fabricated a flag under the old check).
    if not facts:
        return []

    records = run_risk_assessment(document_id, rules, facts, edges)
    return [_to_risk_flag(r) for r in records if r.evaluation_result in _STATUS_BY_RESULT]


router = APIRouter(tags=["risk"])


@router.get("/documents/{document_id}/risk-flags", response_model=list[RiskFlag])
def get_risk_flags(document_id: str) -> list[RiskFlag]:
    return get_risk_flags_for_document(document_id)


app = FastAPI(title="Dev 4 — Risk & Rules Engine API", version="0.1.0")
app.include_router(router)
