"""Tests for saul.py — the HTTP layer. Uses temp fixture dirs for controlled scenarios,
plus real Dev 3 data and a real FastAPI TestClient for end-to-end checks."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import saul
from saul import app, get_risk_flags_for_document

DOC = "DOC-001"

PLAYBOOK = [
    {
        "rule_id": "jurisdiction-allowed", "version": 1, "clause_pattern": "jurisdiction",
        "conditions": [{"field": "jurisdiction", "operator": "not_in", "value": ["California"]}],
        "severity": "medium", "rationale": "test", "allowed_overrides": ["OVERRIDES"],
    },
]

FACTS = [
    {
        "fact_id": "F01", "document_id": DOC, "clause_ref": "3.1", "fact_type": "jurisdiction",
        "value": {"jurisdiction": "New York"}, "confidence": 0.9,
    },
]


def _write_fixtures(tmp: str, edges: list | None = None):
    own = Path(tmp) / "own"
    repo = Path(tmp) / "repo"
    own.mkdir()
    repo.mkdir()
    (own / "sample_playbook.json").write_text(json.dumps(PLAYBOOK))
    (repo / "facts.sample.json").write_text(json.dumps(FACTS))
    (repo / "kg_edges.confirmed.json").write_text(json.dumps(edges or []))
    return own, repo


class GetRiskFlagsForDocumentTests(unittest.TestCase):
    def test_flagged_shape_matches_the_real_contract(self):
        with TemporaryDirectory() as tmp:
            own, repo = _write_fixtures(tmp)
            with patch.object(saul, "OWN_FIXTURES", own), patch.object(saul, "REPO_FIXTURES", repo):
                flags = get_risk_flags_for_document(DOC)
        self.assertEqual(len(flags), 1)
        flag = flags[0]
        self.assertEqual(flag.document_id, DOC)
        self.assertEqual(flag.clause_ref, "3.1")
        self.assertEqual(flag.rule_id, "jurisdiction-allowed")
        self.assertEqual(flag.severity, "medium")
        self.assertEqual(flag.status, "flagged")
        self.assertIsNone(flag.suppressing_edge_id)
        self.assertEqual(flag.triggering_fact_ids, ["F01"])

    def test_suppressed_shape_has_the_overriding_edge_id(self):
        edges = [{
            "edge_id": "E01", "document_id": DOC, "src_clause_ref": "5.0", "dst_clause_ref": "3.1",
            "edge_type": "OVERRIDES", "status": "confirmed",
        }]
        with TemporaryDirectory() as tmp:
            own, repo = _write_fixtures(tmp, edges=edges)
            with patch.object(saul, "OWN_FIXTURES", own), patch.object(saul, "REPO_FIXTURES", repo):
                flags = get_risk_flags_for_document(DOC)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].status, "suppressed")
        self.assertEqual(flags[0].severity, "medium")  # WAIVED still carries severity
        self.assertEqual(flags[0].suppressing_edge_id, "E01")

    def test_compliant_rule_produces_no_risk_flag(self):
        facts = [{**FACTS[0], "value": {"jurisdiction": "California"}}]  # matches the excluded list -> not_in is False
        with TemporaryDirectory() as tmp:
            own, repo = _write_fixtures(tmp)
            (repo / "facts.sample.json").write_text(json.dumps(facts))
            with patch.object(saul, "OWN_FIXTURES", own), patch.object(saul, "REPO_FIXTURES", repo):
                flags = get_risk_flags_for_document(DOC)
        self.assertEqual(flags, [])

    def test_unknown_document_returns_empty_not_a_false_missing_clause_flag(self):
        # Regression test: without the empty-facts guard, a nonexistent document_id got
        # back a fabricated "critical: missing clause" flag from an is_missing rule that
        # can't tell absence-in-a-real-document apart from no-document-at-all. Confirmed
        # via TestClient before this fix existed.
        with TemporaryDirectory() as tmp:
            own, repo = _write_fixtures(tmp)
            with patch.object(saul, "OWN_FIXTURES", own), patch.object(saul, "REPO_FIXTURES", repo):
                flags = get_risk_flags_for_document("DOES-NOT-EXIST")
        self.assertEqual(flags, [])

    def test_zero_facts_with_an_unrelated_edge_present_still_returns_empty(self):
        # Regression: the guard must key on facts alone, not "facts and edges" — an edge
        # existing isn't evidence a document was processed, since edges describe
        # relationships BETWEEN clauses, which presupposes facts were already extracted.
        # Confirmed by testing: the old "facts and edges" guard let this fabricate a flag.
        # Needs an is_missing rule specifically — that's the only rule shape this bug
        # could ever manifest through (see NO_CLAUSE_SENTINEL in gus.py).
        is_missing_playbook = [{
            "rule_id": "has-liability-clause", "version": 1, "clause_pattern": "liability_cap",
            "conditions": [{"field": "amount", "operator": "is_missing"}],
            "severity": "critical", "rationale": "test",
        }]
        edges = [{
            "edge_id": "E99", "document_id": "DOC-REAL", "src_clause_ref": "9.1",
            "dst_clause_ref": "9.2", "edge_type": "MODIFIES", "status": "confirmed",
        }]
        with TemporaryDirectory() as tmp:
            own = Path(tmp) / "own"
            repo = Path(tmp) / "repo"
            own.mkdir()
            repo.mkdir()
            (own / "sample_playbook.json").write_text(json.dumps(is_missing_playbook))
            (repo / "facts.sample.json").write_text("[]")
            (repo / "kg_edges.confirmed.json").write_text(json.dumps(edges))
            with patch.object(saul, "OWN_FIXTURES", own), patch.object(saul, "REPO_FIXTURES", repo):
                flags = get_risk_flags_for_document("DOC-REAL")
        self.assertEqual(flags, [])


class RealDataAndHttpTests(unittest.TestCase):
    """End-to-end: real Dev 3 fixtures, real FastAPI app, real HTTP requests."""

    def test_real_document_over_http_returns_200_and_a_list(self):
        client = TestClient(app)
        resp = client.get(f"/documents/{DOC}/risk-flags")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)
        # Known, still-open Dev 3 bug (the "$1,000,000" string) turns the one rule that
        # would fire into SYSTEM_ERROR instead of FLAGGED, so this is legitimately empty
        # right now, not a sign the endpoint is broken.
        self.assertEqual(resp.json(), [])

    def test_unknown_document_over_http_returns_200_and_empty_list(self):
        client = TestClient(app)
        resp = client.get("/documents/DOES-NOT-EXIST/risk-flags")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_openapi_schema_documents_the_route(self):
        client = TestClient(app)
        schema = client.get("/openapi.json").json()
        self.assertIn("/documents/{document_id}/risk-flags", schema["paths"])


if __name__ == "__main__":
    unittest.main()
