"""The risk-flags read route — replaces the old `dashboard_stubs` stub.

Mirrors `test_api.py`'s pattern: stub the store so the route is exercised without a
database, and pin the behaviour that matters — no confirmed-only default here, since the
dashboard renders suppressed flags too.
"""

import pytest
from fastapi.testclient import TestClient

from app import api
from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.db import get_session
from app.risk import routes as risk_routes
from app.schemas import RiskFlag, RiskFlagStatus

FLAGGED = RiskFlag(
    id="RISK-DOC-001-001",
    document_id="DOC-001",
    clause_ref="2.2",
    rule_id="LIAB-CAP-FLOOR",
    rule_version=3,
    severity="high",
    status=RiskFlagStatus.FLAGGED,
    suppressing_edge_id=None,
    triggering_fact_ids=["DOC-001-C004-F01"],
)
SUPPRESSED = FLAGGED.model_copy(
    update={
        "id": "RISK-DOC-001-002",
        "status": RiskFlagStatus.SUPPRESSED,
        "suppressing_edge_id": "DOC-001-C007-E01",
    }
)


@pytest.fixture
def client(monkeypatch):
    calls: list[str | None] = []

    def fake_list_risk_flags(session, document_id, status=None):
        calls.append(status)
        flags = [FLAGGED, SUPPRESSED]
        return [f for f in flags if status is None or f.status == status]

    monkeypatch.setattr(risk_routes.store, "list_risk_flags", fake_list_risk_flags)
    monkeypatch.setattr(risk_routes, "authorize_document", lambda session, document_id, principal: None)
    api.app.dependency_overrides[get_session] = lambda: None
    api.app.dependency_overrides[get_principal] = lambda: Principal(user_id="u1")

    yield TestClient(api.app), calls
    api.app.dependency_overrides.clear()


def test_risk_flags_route_defaults_to_every_status(client):
    http, calls = client
    response = http.get("/documents/DOC-001/risk-flags")

    assert response.status_code == 200
    assert calls == [None]
    assert [f["id"] for f in response.json()] == ["RISK-DOC-001-001", "RISK-DOC-001-002"]


def test_risk_flags_route_can_filter_by_status(client):
    http, _ = client
    response = http.get("/documents/DOC-001/risk-flags", params={"status": "suppressed"})

    assert [f["id"] for f in response.json()] == ["RISK-DOC-001-002"]
