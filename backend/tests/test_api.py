"""The read boundary.

The behaviour worth pinning is the default: Dev 4 hitting the edges route without a
status parameter must get confirmed edges only. If that default ever flips, the Risk
Engine silently starts trusting unconfirmed relationships.
"""

import pytest
from fastapi.testclient import TestClient

from app import api
from app.db import get_session
from app.schemas import KGEdge, Provenance, ReviewStatus

CONFIRMED = KGEdge(
    edge_id="E1",
    document_id="DOC-001",
    src_clause_ref="4.1",
    dst_clause_ref="2.2",
    edge_type="OVERRIDES",
    status=ReviewStatus.CONFIRMED,
    confidence=0.94,
    evidence_chunk_ids=["DOC-001-C007"],
    resolved_by=Provenance.DIRECT_EXTRACTION,
)
PENDING = CONFIRMED.model_copy(update={"edge_id": "E2", "status": ReviewStatus.PENDING_REVIEW})


@pytest.fixture
def client(monkeypatch):
    """Stubs the store so the route can be exercised without a database."""
    calls: list[ReviewStatus | None] = []

    def fake_list_edges(session, document_id, status=ReviewStatus.CONFIRMED):
        calls.append(status)
        return [e for e in (CONFIRMED, PENDING) if status is None or e.status is status]

    monkeypatch.setattr(api.store, "list_edges", fake_list_edges)
    monkeypatch.setattr(api.store, "list_facts", lambda session, document_id: [])
    api.app.dependency_overrides[get_session] = lambda: None

    yield TestClient(api.app), calls
    api.app.dependency_overrides.clear()


def test_edges_route_defaults_to_confirmed_only(client):
    http, calls = client
    response = http.get("/documents/DOC-001/kg/edges")

    assert response.status_code == 200
    assert calls == [ReviewStatus.CONFIRMED]
    assert [e["edge_id"] for e in response.json()] == ["E1"]
    assert all(e["status"] == "confirmed" for e in response.json())


def test_pending_edges_are_reachable_but_only_on_request(client):
    http, _ = client
    response = http.get("/documents/DOC-001/kg/edges", params={"status": "pending_review"})

    assert [e["edge_id"] for e in response.json()] == ["E2"]


def test_unknown_status_is_rejected(client):
    """The three-value enum is the contract; 'pending' is not one of them."""
    http, _ = client
    assert http.get("/documents/DOC-001/kg/edges", params={"status": "pending"}).status_code == 422


def test_facts_route_responds(client):
    http, _ = client
    assert http.get("/documents/DOC-001/facts").status_code == 200
