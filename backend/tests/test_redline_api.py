"""Serving redlines, and resolving the ones that were held.

Two things are pinned. `GET /documents/{id}/redlines` must default to confirmed only — a
held redline is one the loop could not stand behind, and serving it as a suggestion is
exactly the §3.4 failure the whole stage is built to avoid. And resolving a redline
escalation must write back to the *redline row*, for the same reason resolving an edge
escalation writes back to the edge: until the target flips, the reviewer's decision is
invisible to everyone downstream.

Route tests stub the store; the resolve test drives `store.resolve_escalation` against a
fake session, copying `test_review_queue.py` — Postgres-only column types rule out an
in-memory SQLite double.
"""

import pytest
from fastapi.testclient import TestClient

from app import api, models
from app.db import get_session
from app.kg import store as kg_store
from app.redline import store as redline_store
from app.schemas import EscalationReason, EscalationSource, Redline, ReviewStatus, TraceRound

CONFIRMED = Redline(
    redline_id="RL-DOC-001-001",
    document_id="DOC-001",
    risk_id="RISK-DOC-001-001",
    clause_ref="2.2",
    status=ReviewStatus.CONFIRMED,
    confidence=0.85,
    original_text="... shall not exceed one million dollars ($1,000,000).",
    suggested_text="... shall not exceed five million dollars ($5,000,000).",
    rationale="Below the playbook floor.",
    rounds_attempted=3,
    trace=[TraceRound(round=1, attempt="definition", result="found", resolved=False)],
    grounding_chunk_ids=["DOC-001-C004"],
    grounding_edge_ids=["DOC-001-C007-E01"],
)
HELD = CONFIRMED.model_copy(
    update={
        "redline_id": "RL-DOC-001-002",
        "risk_id": "RISK-DOC-001-002",
        "clause_ref": "2.4",
        "status": ReviewStatus.PENDING_REVIEW,
        "confidence": 0.70,
    }
)


@pytest.fixture
def client(monkeypatch):
    calls: dict = {}
    rows = [CONFIRMED, HELD]

    def fake_list(session, document_id, status=ReviewStatus.CONFIRMED):
        calls["list"] = {"document_id": document_id, "status": status}
        return [r for r in rows if r.document_id == document_id and (status is None or r.status is status)]

    def fake_get(session, redline_id):
        return next((r for r in rows if r.redline_id == redline_id), None)

    monkeypatch.setattr(redline_store, "list_redlines", fake_list)
    monkeypatch.setattr(redline_store, "get_redline", fake_get)

    api.app.dependency_overrides[get_session] = lambda: object()
    yield TestClient(api.app), calls
    api.app.dependency_overrides.clear()


def test_redlines_default_to_confirmed_only(client):
    http, calls = client
    response = http.get("/documents/DOC-001/redlines")
    assert response.status_code == 200
    assert calls["list"]["status"] is ReviewStatus.CONFIRMED
    assert [r["redline_id"] for r in response.json()] == [CONFIRMED.redline_id]


def test_a_held_redline_is_reachable_but_only_on_request(client):
    http, _ = client
    response = http.get("/documents/DOC-001/redlines", params={"status": "pending_review"})
    assert [r["redline_id"] for r in response.json()] == [HELD.redline_id]


def test_the_served_redline_carries_its_retrieval_trace(client):
    """§4.1 — the grounding is the audit record, not debug output."""
    http, _ = client
    body = http.get(f"/redlines/{HELD.redline_id}").json()
    assert body["rounds_attempted"] == 3
    assert body["trace"][0]["attempt"] == "definition"
    assert body["grounding_chunk_ids"] == ["DOC-001-C004"]


def test_fetching_one_redline_ignores_status(client):
    """The Review Queue links here, and a reviewer must be able to see what they are
    approving."""
    http, _ = client
    assert http.get(f"/redlines/{HELD.redline_id}").status_code == 200
    assert http.get("/redlines/nope").status_code == 404


def test_unknown_document_serves_nothing(client):
    http, _ = client
    assert http.get("/documents/DOC-999/redlines").json() == []


# --- resolve_escalation: approving a held redline has to flip the redline row ---


class FakeSession:
    def __init__(self, *rows) -> None:
        self.rows = {(type(r).__name__, _pk(r)): r for r in rows}

    def get(self, model, pk):
        return self.rows.get((model.__name__, pk))

    def flush(self) -> None:
        pass


def _pk(row):
    return row.id if isinstance(row, models.Escalation) else row.redline_id


def _rows():
    escalation = models.Escalation(
        id="ESC-RL-DOC-001-002",
        status=ReviewStatus.PENDING_REVIEW.value,
        source=EscalationSource.REDLINE_GENERATOR.value,
        reason=EscalationReason.LOW_CONFIDENCE.value,
        document_id="DOC-001",
        clause_ref="2.4",
        rounds_attempted=2,
        trace=[],
        reviewer_id=None,
        resolved_at=None,
        target_edge_id=None,
        target_redline_id=HELD.redline_id,
    )
    redline = models.Redline(
        redline_id=HELD.redline_id,
        document_id="DOC-001",
        risk_id=HELD.risk_id,
        clause_ref="2.4",
        status=ReviewStatus.PENDING_REVIEW.value,
        confidence=0.70,
        original_text=HELD.original_text,
        suggested_text=HELD.suggested_text,
        rationale=HELD.rationale,
        rounds_attempted=2,
        trace=[],
        grounding_chunk_ids=["DOC-001-C005"],
        grounding_edge_ids=[],
    )
    return escalation, redline


@pytest.mark.parametrize("decision", [ReviewStatus.CONFIRMED, ReviewStatus.REJECTED])
def test_resolving_flips_the_redline_row(decision):
    escalation, redline = _rows()
    session = FakeSession(escalation, redline)

    item = kg_store.resolve_escalation(
        session, escalation.id, status=decision, reviewer_id="ada"
    )

    assert item.status is decision
    assert item.target_redline_id == redline.redline_id
    assert escalation.reviewer_id == "ada"
    assert escalation.resolved_at is not None
    # The write that matters: `list_redlines` defaults to confirmed, so approval only
    # means something once the redline itself moves.
    assert redline.status == decision.value


def test_resolving_a_budget_exhausted_item_has_no_redline_to_flip():
    """§4.1 wrote no redline for it, so the escalation targets nothing.

    The reviewer's decision still records — they are acknowledging that the agent could
    not ground a rewrite, which is itself the outcome.
    """
    escalation, redline = _rows()
    escalation.id = "ESC-RL-DOC-001-004"
    escalation.reason = EscalationReason.BUDGET_EXHAUSTED.value
    escalation.target_redline_id = None
    session = FakeSession(escalation, redline)

    item = kg_store.resolve_escalation(
        session, escalation.id, status=ReviewStatus.REJECTED, reviewer_id="ada"
    )

    assert item.status is ReviewStatus.REJECTED
    assert redline.status == ReviewStatus.PENDING_REVIEW.value
