"""TEMPORARY — stub read for the Compliance / Risk dashboard.

Dev 4's Risk & Rules Engine (§3.3) writes `risk_flags` to the Metadata DB and does not
exist yet, so this route serves the hand-written fixture and the dashboard can be built
against a real shape now.

Delete this module when that stage publishes. The route path is the contract — swap the
fixture read for a `store.list_risk_flags` call and the frontend does not change.

`GET /documents/{id}/redlines` used to be stubbed here too. It is real now, in
`app.redline.routes`, and it defaults to confirmed redlines only — the stub had no
concept of a held redline and served every row.

Facts and confirmed KG edges are NOT stubbed — they come from the real routes in
`app.api`.
"""

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

router = APIRouter(tags=["dashboard (stub)"])


@lru_cache(maxsize=None)
def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@router.get("/documents/{document_id}/risk-flags")
def get_risk_flags(document_id: str) -> list[dict]:
    """STUB — Dev 4's output. Severity keyed to the clause and playbook rule version."""
    return [r for r in _load("risk.sample.json") if r["document_id"] == document_id]
