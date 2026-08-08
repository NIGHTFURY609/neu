"""app.orchestration chains Clause NER -> Risk -> Redline against a document's real
chunks — the wiring that used to only exist as three separate manual `--persist` runs,
each against a hand-authored JSON fixture (see app.pipeline / app.risk.pipeline /
app.redline.pipeline's own CLIs and their docstrings).

Store writes are stubbed here, same convention as test_api.py / test_risk_api.py /
test_review_queue.py: this file is about the chain calling the right stage with the right
data in the right order, not about persistence, which is each store module's own concern.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import orchestration
from app.risk.rules import load_playbook
from app.schemas import Chunk

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class _FakeSession:
    """process_document only ever calls `.commit()` directly on the session — every
    actual write goes through a store function, and those are monkeypatched below."""

    def commit(self) -> None:
        pass


@pytest.fixture
def sample_chunks() -> list[Chunk]:
    payload = json.loads((FIXTURES / "chunks.sample.json").read_text(encoding="utf-8"))
    return [Chunk.model_validate(c) for c in payload["chunks"]]


@pytest.fixture
def recorder(monkeypatch):
    """Capture what each store call would have written instead of writing it."""
    calls = {
        "facts": [], "edges": [], "escalations": [],
        "risk_flags": [], "redlines": [], "redline_escalations": [],
    }
    monkeypatch.setattr(orchestration.kg_store, "save_facts", lambda s, v: calls["facts"].extend(v))
    monkeypatch.setattr(orchestration.kg_store, "save_edges", lambda s, v: calls["edges"].extend(v))
    monkeypatch.setattr(orchestration.kg_store, "save_escalations", lambda s, v: calls["escalations"].extend(v))
    monkeypatch.setattr(orchestration.risk_store, "save_risk_flags", lambda s, v: calls["risk_flags"].extend(v))
    monkeypatch.setattr(orchestration.redline_store, "save_redlines", lambda s, v: calls["redlines"].extend(v))
    monkeypatch.setattr(
        orchestration.redline_store, "save_redline_escalations",
        lambda s, v: calls["redline_escalations"].extend(v),
    )
    return calls


def _active_playbook(sample_playbook):
    """What `app.playbook_store.load_active_playbook` would return for DOC-001's
    playbook, without a database round trip."""
    risk_rules = load_playbook(str(FIXTURES / "playbook.sample.json"))
    return risk_rules, sample_playbook


def test_full_chain_produces_facts_risk_flags_and_a_redline_outcome(
    monkeypatch, recorder, sample_chunks, sample_playbook
):
    """Real chunks through all three stages, nothing hand-wired in between.

    DOC-001 is built so a risk flag both fires and either grounds a redline or escalates
    (backend/README.md: "all four outcomes are reachable at the default") — asserting
    that instead of an exact count keeps this test from re-encoding the sample document.
    """
    monkeypatch.setattr(orchestration, "load_active_playbook", lambda session, jurisdiction=None: _active_playbook(sample_playbook))

    summary = orchestration.process_document(_FakeSession(), "DOC-001", sample_chunks)

    assert summary.facts > 0 and summary.facts == len(recorder["facts"])
    assert summary.risk_flags > 0 and summary.risk_flags == len(recorder["risk_flags"])
    assert summary.redlines == len(recorder["redlines"])
    assert summary.redlines > 0 or recorder["redline_escalations"]


def test_no_active_playbook_stops_after_clause_ner(monkeypatch, recorder, sample_chunks):
    monkeypatch.setattr(orchestration, "load_active_playbook", lambda session, jurisdiction=None: ([], []))

    summary = orchestration.process_document(_FakeSession(), "DOC-001", sample_chunks)

    assert summary.facts > 0
    assert recorder["facts"]  # Clause NER's output was still persisted
    assert summary.risk_flags == 0
    assert summary.redlines == 0
    assert recorder["risk_flags"] == []
    assert recorder["redlines"] == []


def test_clause_ner_failure_short_circuits_the_whole_chain(monkeypatch, recorder, sample_chunks):
    def _boom(chunks):
        raise RuntimeError("boom")

    monkeypatch.setattr(orchestration.clause_ner_pipeline, "run", _boom)

    summary = orchestration.process_document(_FakeSession(), "DOC-001", sample_chunks)

    # Nothing was extracted, and the summary says why. The counts alone are ambiguous —
    # zero facts is also what a genuinely empty document produces — so `stage` and
    # `error` are what let `app.processing` record this as failed rather than succeeded.
    assert summary.facts == 0
    assert summary.stage == "clause_ner"
    assert summary.error is not None and "boom" in summary.error
    assert recorder["facts"] == []
    assert recorder["risk_flags"] == []
    assert recorder["redlines"] == []


def test_risk_assessment_failure_keeps_the_ner_output_already_committed(
    monkeypatch, recorder, sample_chunks, sample_playbook
):
    """A failure mid-chain must not undo what the earlier stage already committed —
    `process_document`'s own docstring guarantee."""
    monkeypatch.setattr(orchestration, "load_active_playbook", lambda session, jurisdiction=None: _active_playbook(sample_playbook))

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(orchestration.risk_pipeline, "run_risk_assessment", _boom)

    summary = orchestration.process_document(_FakeSession(), "DOC-001", sample_chunks)

    assert summary.facts > 0
    assert recorder["facts"]  # already committed, not rolled back
    assert summary.risk_flags == 0
    assert recorder["risk_flags"] == []
