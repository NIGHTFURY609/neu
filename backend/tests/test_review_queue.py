"""The Review Queue — Dev 1.

Two things are worth pinning here.

The first is the default filter: `GET /review-queue` with no arguments must return the
open queue, not everything ever resolved. The second, and the one that actually matters
downstream, is that resolving an escalation writes back to the *candidate KG edge*.
`list_edges` defaults to confirmed, so a resolution that only touches the escalation row
is invisible to the Risk Engine — the reviewer's decision would silently do nothing.

The route tests stub the store (no database); the resolve tests drive
`store.resolve_escalation` against a fake session so the two writes can be inspected
directly. Postgres-only column types (JSONB, ARRAY) rule out an in-memory SQLite double.
"""

import pytest
from fastapi.testclient import TestClient

from app import api, models
from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.db import get_session
from app.kg import store
from app import review_queue
from app.schemas import (
    EdgeType,
    EscalationItem,
    EscalationReason,
    EscalationSource,
    Provenance,
    ReviewStatus,
    TraceRound,
)

PENDING = EscalationItem(
    id="ESC-DOC-001-C011-E01",
    target_edge_id="DOC-001-C011-E01",
    status=ReviewStatus.PENDING_REVIEW,
    source=EscalationSource.CLAUSE_NER,
    reason=EscalationReason.AMBIGUOUS_EDGE,
    document_id="DOC-001",
    clause_ref="8.2",
    rounds_attempted=3,
    trace=[TraceRound(round=1, attempt="kg_precedent", result="no match", resolved=False)],
)
RESOLVED = PENDING.model_copy(
    update={"id": "ESC-DOC-001-C099-E01", "status": ReviewStatus.CONFIRMED, "reviewer_id": "ada"}
)


@pytest.fixture
def client(monkeypatch):
    """Stubs the store so the routes are exercised without a database."""
    calls: dict = {}
    items = {PENDING.id: PENDING, RESOLVED.id: RESOLVED}

    def fake_list(session, *, statuses=None, document_id=None, source=None, principal=None):
        calls["list"] = {
            "statuses": statuses,
            "document_id": document_id,
            "source": source,
            "principal": principal,
        }
        return [i for i in items.values() if not statuses or i.status in statuses]

    def fake_get(session, escalation_id):
        return items.get(escalation_id)

    def fake_resolve(session, escalation_id, *, status, reviewer_id, edge_type=None):
        calls["resolve"] = {
            "id": escalation_id,
            "status": status,
            "reviewer_id": reviewer_id,
            "edge_type": edge_type,
        }
        return items[escalation_id].model_copy(
            update={"status": status, "reviewer_id": reviewer_id}
        )

    monkeypatch.setattr(store, "list_escalations", fake_list)
    monkeypatch.setattr(store, "get_escalation", fake_get)
    monkeypatch.setattr(store, "resolve_escalation", fake_resolve)
    # `_Session` below has no `.get`, so `authorize_document` (which would otherwise
    # fetch a real row) is stubbed too — these tests stub the store, not the database.
    monkeypatch.setattr(review_queue, "authorize_document", lambda session, document_id, principal: None)

    class _Session:
        def commit(self) -> None:
            calls["committed"] = True

    api.app.dependency_overrides[get_session] = lambda: _Session()
    # Attribution comes from the authenticated caller now, not the request body, so the
    # reviewer identity these tests assert on has to be injected here.
    api.app.dependency_overrides[get_principal] = lambda: Principal(
        user_id="ada", role="reviewer", rbac_tags=frozenset({"*"})
    )
    yield TestClient(api.app), calls
    api.app.dependency_overrides.clear()


def test_queue_defaults_to_the_open_pile(client):
    http, calls = client
    response = http.get("/review-queue")

    assert response.status_code == 200
    assert calls["list"]["statuses"] == [ReviewStatus.PENDING_REVIEW]
    assert [i["id"] for i in response.json()] == [PENDING.id]


def test_filters_are_passed_through(client):
    http, calls = client
    response = http.get(
        "/review-queue",
        params={
            "status": ["pending_review", "confirmed"],
            "document_id": "DOC-001",
            "source": "clause_ner",
        },
    )

    assert response.status_code == 200
    assert calls["list"]["statuses"] == [ReviewStatus.PENDING_REVIEW, ReviewStatus.CONFIRMED]
    assert calls["list"]["document_id"] == "DOC-001"
    assert calls["list"]["source"] is EscalationSource.CLAUSE_NER
    # The queue is RBAC-filtered in SQL, so the principal has to reach the store. It is
    # asserted separately rather than as part of an exact-dict comparison because the
    # value is an object whose repr would make any future failure here unreadable.
    assert calls["list"]["principal"].user_id == "ada"


def test_served_item_carries_the_retry_trace(client):
    """The whole point of the queue UI — a reviewer must see what the agent already tried."""
    http, _ = client
    item = http.get(f"/review-queue/{PENDING.id}").json()

    assert item["rounds_attempted"] == 3
    assert item["trace"][0]["attempt"] == "kg_precedent"
    assert item["target_edge_id"] == "DOC-001-C011-E01"


def test_unknown_id_is_404(client):
    http, _ = client
    assert http.get("/review-queue/nope").status_code == 404
    assert http.post("/review-queue/nope/resolve", json=RESOLVE_BODY).status_code == 404


# No `reviewer_id`: it is taken from the authenticated principal, not the body.
RESOLVE_BODY = {"status": "confirmed", "edge_type": "OVERRIDES"}


def test_resolve_commits_and_returns_the_updated_item(client):
    http, calls = client
    response = http.post(f"/review-queue/{PENDING.id}/resolve", json=RESOLVE_BODY)

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert calls["resolve"]["edge_type"] is EdgeType.OVERRIDES
    assert calls["committed"] is True


def test_attribution_comes_from_the_session_not_the_request_body(client):
    """A reviewer cannot resolve as somebody else.

    §4.2 promises a human-confirmed fact stays distinguishable from an AI-generated one.
    That promise is only worth anything if the *human* is verified too — while
    `reviewer_id` was a free-text field, anyone could sign anyone else's name to a
    decision, and the audit trail recorded it without complaint.
    """
    http, calls = client
    response = http.post(
        f"/review-queue/{PENDING.id}/resolve",
        json={**RESOLVE_BODY, "reviewer_id": "someone-else"},
    )

    assert response.status_code == 200
    assert calls["resolve"]["reviewer_id"] == "ada"  # the principal, not the body
    assert response.json()["reviewer_id"] == "ada"


def test_resolving_twice_is_409(client):
    """Someone else got there first. Say so rather than overwriting their decision."""
    http, calls = client
    response = http.post(f"/review-queue/{RESOLVED.id}/resolve", json=RESOLVE_BODY)

    assert response.status_code == 409
    assert "resolve" not in calls


@pytest.mark.parametrize(
    "body",
    [
        {"status": "pending_review"},  # not a decision
        {"status": "pending"},  # the enum-drift typo
        {},  # no decision at all
        # The two "unattributable" cases that used to live here — an empty and a missing
        # `reviewer_id` — are gone with the field. Attribution can no longer be supplied
        # by the caller, so it can no longer be omitted; see the test above.
    ],
)
def test_invalid_resolutions_are_rejected(client, body):
    http, _ = client
    assert http.post(f"/review-queue/{PENDING.id}/resolve", json=body).status_code == 422


# --- store.resolve_escalation: the write-back that makes resolution mean something ---


class FakeSession:
    """Enough of a Session for `resolve_escalation`: get by primary key, plus flush."""

    def __init__(self, *rows) -> None:
        self.rows = {(type(r).__name__, _pk(r)): r for r in rows}

    def get(self, model, pk):
        return self.rows.get((model.__name__, pk))

    def flush(self) -> None:
        pass


def _pk(row):
    return row.id if isinstance(row, models.Escalation) else row.edge_id


def _rows():
    escalation = models.Escalation(
        id=PENDING.id,
        status=ReviewStatus.PENDING_REVIEW.value,
        source=EscalationSource.CLAUSE_NER.value,
        reason=EscalationReason.AMBIGUOUS_EDGE.value,
        document_id="DOC-001",
        clause_ref="8.2",
        rounds_attempted=3,
        trace=[],
        reviewer_id=None,
        resolved_at=None,
        target_edge_id="DOC-001-C011-E01",
    )
    edge = models.KGEdge(
        edge_id="DOC-001-C011-E01",
        document_id="DOC-001",
        src_clause_ref="8.2",
        dst_clause_ref="3.1",
        edge_type=EdgeType.OVERRIDES.value,
        status=ReviewStatus.PENDING_REVIEW.value,
        confidence=0.41,
        evidence_chunk_ids=["DOC-001-C011"],
        pattern_key="in_lieu_of",
        resolved_by=None,
    )
    return escalation, edge


def test_resolve_flips_the_candidate_edge_and_tags_it_human():
    escalation, edge = _rows()
    session = FakeSession(escalation, edge)

    item = store.resolve_escalation(
        session, PENDING.id, status=ReviewStatus.CONFIRMED, reviewer_id="ada"
    )

    assert item.status is ReviewStatus.CONFIRMED
    assert escalation.reviewer_id == "ada"
    assert escalation.resolved_at is not None
    # Without this the Risk Engine's default `list_edges` call still can't see the edge.
    assert edge.status == ReviewStatus.CONFIRMED.value
    # §4.2: human-confirmed must stay distinguishable from AI-generated, permanently.
    assert edge.resolved_by == Provenance.HUMAN.value


def test_reviewer_can_pin_the_correct_edge_type():
    """The edge was escalated because the extractor couldn't choose. The human can."""
    escalation, edge = _rows()

    store.resolve_escalation(
        FakeSession(escalation, edge),
        PENDING.id,
        status=ReviewStatus.CONFIRMED,
        reviewer_id="ada",
        edge_type=EdgeType.WAIVES,
    )

    assert edge.edge_type == EdgeType.WAIVES.value


def test_rejecting_leaves_the_edge_out_of_the_confirmed_set():
    escalation, edge = _rows()

    store.resolve_escalation(
        FakeSession(escalation, edge),
        PENDING.id,
        status=ReviewStatus.REJECTED,
        reviewer_id="ada",
    )

    assert edge.status == ReviewStatus.REJECTED.value
    assert edge.resolved_by == Provenance.HUMAN.value


def test_escalation_without_a_target_edge_resolves_cleanly():
    """The Redline Generator's budget_exhausted path has no edge to write back to."""
    escalation, _ = _rows()
    escalation.target_edge_id = None

    item = store.resolve_escalation(
        FakeSession(escalation), PENDING.id, status=ReviewStatus.CONFIRMED, reviewer_id="ada"
    )

    assert item.status is ReviewStatus.CONFIRMED


def test_missing_escalation_raises():
    with pytest.raises(LookupError):
        store.resolve_escalation(
            FakeSession(), "nope", status=ReviewStatus.CONFIRMED, reviewer_id="ada"
        )
