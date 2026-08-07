"""End-to-end behaviour of the stage, and anti-drift on the fixtures we publish.

Dev 4 and Dev 1 build against the files in `fixtures/`. If those stop matching the
models, their integration breaks at the seam — so validate them here, in our own suite,
rather than finding out at integration.
"""

import json

import pytest

from app.pipeline import run
from app.schemas import (
    EscalationRecord,
    Fact,
    KGEdge,
    Provenance,
    ReviewStatus,
)
from tests.conftest import FIXTURES


# ------------------------------------------------------------------ stage behaviour


def test_retry_loop_keeps_most_ambiguities_out_of_the_queue(sample_chunks):
    """The whole point of §3.2's bounded retry: fewer humans, not more.

    Four ambiguous candidates in this document. Without the loop every one of them is a
    queue item; with it, only the genuinely unclear clause escalates.
    """
    result = run(sample_chunks)
    resolved_by_retry = [
        e for e in result.confirmed_edges if e.resolved_by and e.resolved_by.value.startswith("retry:")
    ]

    assert len(resolved_by_retry) == 3
    assert len(result.escalations) == 1


def test_each_strategy_resolves_something_end_to_end(sample_chunks):
    result = run(sample_chunks)
    provenances = {e.resolved_by for e in result.confirmed_edges}

    assert provenances == {
        Provenance.DIRECT_EXTRACTION,
        Provenance.RETRY_KG_PRECEDENT,
        Provenance.RETRY_WIDEN_CONTEXT,
        Provenance.RETRY_ALTERNATE_PARSE,
    }


def test_escalated_edge_stays_pending_and_carries_its_trace(sample_chunks):
    result = run(sample_chunks)
    escalation_id, edge_id, record = result.escalations[0]
    pending = {e.edge_id: e for e in result.pending_edges}

    assert edge_id in pending
    assert pending[edge_id].status is ReviewStatus.PENDING_REVIEW
    # Never attributed to anything — a human has not touched it and neither did a
    # strategy that worked.
    assert pending[edge_id].resolved_by is None
    assert escalation_id.endswith(edge_id)
    assert record.rounds_attempted == 3
    assert record.clause_ref == "8.2"


def test_no_pending_edge_is_ever_marked_confirmed(sample_chunks):
    result = run(sample_chunks)
    assert {e.status for e in result.confirmed_edges} == {ReviewStatus.CONFIRMED}
    assert {e.status for e in result.pending_edges} == {ReviewStatus.PENDING_REVIEW}
    assert not set(e.edge_id for e in result.confirmed_edges) & set(
        e.edge_id for e in result.pending_edges
    )


def test_ocr_confidence_caps_extraction_confidence(sample_chunks):
    """§7: garbage OCR silently degrades everything downstream unless it is surfaced.

    A fact cannot be more trustworthy than the page it was read off.
    """
    by_ref = {c.clause_ref: c for c in sample_chunks}
    result = run(sample_chunks)

    for fact in result.facts:
        assert fact.confidence <= by_ref[fact.clause_ref].ocr_confidence


def test_prior_confirmed_edges_are_usable_as_precedent(sample_chunks):
    """Re-running with the previous run's edges must not change the outcome."""
    first = run(sample_chunks)
    second = run(sample_chunks, prior_edges=first.confirmed_edges)

    assert [e.edge_type for e in second.confirmed_edges] == [
        e.edge_type for e in first.confirmed_edges
    ]
    assert len(second.escalations) == len(first.escalations)


# ------------------------------------------------------------------ published fixtures


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name, model",
    [
        ("facts.sample.json", Fact),
        ("kg_edges.confirmed.json", KGEdge),
        ("kg_edges.pending_review.json", KGEdge),
    ],
)
def test_published_fixtures_validate(name, model):
    items = _load(name)
    assert items, f"{name} is empty — downstream devs have nothing to build against"
    for item in items:
        model.model_validate(item)


def test_published_escalation_fixture_validates():
    items = _load("escalation.sample.json")
    assert items
    for item in items:
        EscalationRecord.model_validate(item)


def test_published_confirmed_fixture_contains_no_pending_edges():
    for item in _load("kg_edges.confirmed.json"):
        assert item["status"] == ReviewStatus.CONFIRMED


def test_published_fixtures_match_a_fresh_run(sample_chunks):
    """Guard against someone editing a fixture by hand instead of regenerating it."""
    result = run(sample_chunks)
    assert _load("kg_edges.confirmed.json") == [
        e.model_dump(mode="json") for e in result.confirmed_edges
    ]
    assert _load("facts.sample.json") == [f.model_dump(mode="json") for f in result.facts]
