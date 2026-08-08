"""The Redline Generator's active retrieval loop — ARCHITECTURE.md §3.4 and §4.1.

§3.4 names this stage the highest hallucination risk in the system, so most of what is
pinned here is about what the loop must *refuse* to do: write a redline it could not
ground, serve one it is not confident in, or read a relationship the Knowledge Graph has
not settled.

The sample fixtures are chosen so a single run reaches all four outcomes — confirmed,
held for review, budget exhausted, suppressed. That is deliberate: an exit condition
only reachable by a purpose-built test is one that rots.
"""

import pytest

from app.config import settings
from app.redline.generator import generate, run
from app.redline.grounding import assess
from app.redline.provider import MockRedlineProvider
from app.redline.retrieval import RetrievalContext, RetrievalState
from app.schemas import (
    EscalationReason,
    EscalationSource,
    KGEdge,
    ReviewStatus,
    RiskFlagStatus,
)
from tests.conftest import load


@pytest.fixture
def result(sample_risks, sample_playbook, retrieval_context):
    return run(sample_risks, sample_playbook, retrieval_context, MockRedlineProvider())


def _rule(playbook, risk):
    return next(r for r in playbook if (r.rule_id, r.version) == (risk.rule_id, risk.rule_version))


def _risk(risks, risk_id):
    return next(r for r in risks if r.id == risk_id)


# ------------------------------------------------------------------ the four §4.1 exits


def test_one_run_reaches_every_exit(result):
    assert [r.status for r in result.redlines] == [
        ReviewStatus.CONFIRMED,
        ReviewStatus.PENDING_REVIEW,
    ]
    assert {record.reason for _, _, record in result.escalations} == {
        EscalationReason.LOW_CONFIDENCE,
        EscalationReason.BUDGET_EXHAUSTED,
    }
    assert [r.id for r in result.suppressed] == ["RISK-DOC-001-003"]


def test_suppressed_flags_never_produce_a_redline(result, sample_risks, sample_playbook,
                                                  retrieval_context):
    """The Risk Engine already cleared these against a confirmed edge.

    Redlining one would quietly undo a decision the deterministic stage made on purpose,
    so `run` skips it and `generate` refuses it outright.
    """
    suppressed = _risk(sample_risks, "RISK-DOC-001-003")
    assert suppressed.status is RiskFlagStatus.SUPPRESSED
    assert suppressed.id not in {r.risk_id for r in result.redlines}
    assert suppressed.id not in {r.risk_id for r in result.results}

    with pytest.raises(ValueError):
        generate(suppressed, _rule(sample_playbook, suppressed), retrieval_context)


def test_budget_exhaustion_writes_no_redline_row(result):
    """§4.1's strictest clause: do not write an unconfirmed redline.

    `list_redlines` defaults to confirmed, so a held row would not be served either — but
    "we never wrote it" is a stronger guarantee than "we wrote it and filtered it", and
    it is the one the spec asks for.
    """
    exhausted = next(
        r for r in result.results
        if r.escalation and r.escalation[1].reason is EscalationReason.BUDGET_EXHAUSTED
    )
    assert exhausted.redline is None
    assert exhausted.risk_id not in {r.risk_id for r in result.redlines}

    _, target_redline_id, record = next(
        e for e in result.escalations if e[2].reason is EscalationReason.BUDGET_EXHAUSTED
    )
    assert target_redline_id is None
    assert record.rounds_attempted == settings.retrieval_budget


def test_a_held_redline_is_escalated_and_points_back_at_itself(result):
    """Exit 3: grounded, but not confident enough to serve.

    The redline exists so a reviewer has something concrete to approve; the escalation is
    what stops it being shown as a suggestion in the meantime, and `target_redline_id` is
    what lets the approval flip it.
    """
    held = next(r for r in result.redlines if r.status is ReviewStatus.PENDING_REVIEW)
    escalation_id, target_redline_id, record = next(
        e for e in result.escalations if e[2].reason is EscalationReason.LOW_CONFIDENCE
    )
    assert target_redline_id == held.redline_id
    assert escalation_id == f"ESC-{held.redline_id}"
    assert record.clause_ref == held.clause_ref


def test_nothing_below_the_threshold_is_ever_confirmed(result):
    for redline in result.redlines:
        confirmed = redline.status is ReviewStatus.CONFIRMED
        assert confirmed == (redline.confidence >= settings.redline_confidence)


def test_every_escalation_carries_its_trace(result):
    """A queue item without a trace is a shrug. §4.2 requires the reviewer sees the work."""
    for escalation_id, _, record in result.escalations:
        assert record.source is EscalationSource.REDLINE_GENERATOR
        assert record.trace, escalation_id
        assert record.rounds_attempted == len(record.trace)
        assert record.status is ReviewStatus.PENDING_REVIEW


# ------------------------------------------------------------------ grounding discipline


def test_redlines_name_the_evidence_they_were_written_from(result):
    for redline in result.redlines:
        assert redline.grounding_chunk_ids
        assert redline.rounds_attempted == len(redline.trace)
        # The flagged clause is the seed, so it is always among the grounding.
        assert redline.original_text


def test_the_loop_stops_the_moment_it_is_grounded(result, sample_risks, sample_playbook,
                                                  retrieval_context):
    """Only the last round may be `resolved`, and a grounded run must not keep querying."""
    for redline in result.redlines:
        assert [t.resolved for t in redline.trace] == [False] * (len(redline.trace) - 1) + [True]

    risk = _risk(sample_risks, "RISK-DOC-001-001")
    outcome = generate(risk, _rule(sample_playbook, risk), retrieval_context)
    assert outcome.redline.rounds_attempted < settings.retrieval_budget


def test_retrieval_never_surfaces_a_pending_edge(result, retrieval_context):
    """§3.3 — a `pending_review` edge is treated as absent, not as weak evidence.

    Enforced at construction, so it holds for every query the loop can make rather than
    for the ones this test happens to exercise.
    """
    assert {e.status for e in retrieval_context.confirmed_edges} == {ReviewStatus.CONFIRMED}
    confirmed_ids = {e.edge_id for e in retrieval_context.confirmed_edges}
    for redline in result.redlines:
        assert set(redline.grounding_edge_ids) <= confirmed_ids


def test_the_context_refuses_unconfirmed_edges(sample_chunks):
    """It raises rather than filtering. Silently dropping them would make a context built
    from the wrong list look like one built from the right one."""
    with pytest.raises(ValueError, match="non-confirmed"):
        RetrievalContext(
            chunks=sample_chunks,
            confirmed_edges=[KGEdge.model_validate(e) for e in load("kg_edges.pending_review.json")],
        )


def test_an_unsettled_pair_is_a_gap_retrieval_cannot_close(sample_risks, sample_playbook,
                                                           retrieval_context):
    """The 8.2 case, which is why the budget exists.

    The Knowledge Graph holds only a `pending_review` edge between 8.2 and 3.1, so the
    loop can read both clauses and still not know which controls. It has to spend its
    budget proving the answer is not in the document before escalating — the trace is
    what tells the reviewer it did.
    """
    risk = _risk(sample_risks, "RISK-DOC-001-004")
    outcome = generate(risk, _rule(sample_playbook, risk), retrieval_context)

    assert outcome.redline is None
    assert outcome.escalation[1].reason is EscalationReason.BUDGET_EXHAUSTED
    assert not any(t.resolved for t in outcome.escalation[1].trace)
    assert "3.1" in retrieval_context.unresolved_partners("8.2")


def test_assess_is_empty_exactly_when_a_redline_was_written(sample_risks, sample_playbook,
                                                            retrieval_context):
    """`assess() == []` is the definition of sufficient grounding, not a heuristic."""
    for risk in sample_risks:
        if risk.status is RiskFlagStatus.SUPPRESSED:
            continue
        rule = _rule(sample_playbook, risk)
        outcome = generate(risk, rule, retrieval_context)
        state = RetrievalState()
        state.chunks.extend(
            c for c in retrieval_context.chunks
            if c.chunk_id in (outcome.redline.grounding_chunk_ids if outcome.redline else [])
        )
        if outcome.redline is not None:
            assert assess(risk, rule, retrieval_context, state) == []


# ------------------------------------------------------------------ §7 confidence cap


def test_ocr_confidence_caps_redline_confidence(result, retrieval_context):
    """A rewrite is no more trustworthy than the worst page it was read off."""
    by_id = {c.chunk_id: c for c in retrieval_context.chunks}
    for redline in result.redlines:
        worst = min(by_id[i].ocr_confidence for i in redline.grounding_chunk_ids)
        assert redline.confidence <= worst


def test_a_bad_scan_pulls_a_redline_below_the_serve_threshold(sample_risks, sample_playbook,
                                                              retrieval_context):
    """The cap has to be able to change the outcome, not just the number.

    Degrade the flagged clause's page to something barely legible and the redline that
    was confirmed must stop being served.
    """
    risk = _risk(sample_risks, "RISK-DOC-001-001")
    rule = _rule(sample_playbook, risk)
    assert generate(risk, rule, retrieval_context).redline.status is ReviewStatus.CONFIRMED

    retrieval_context.clause(risk.clause_ref).ocr_confidence = 0.42
    degraded = generate(risk, rule, retrieval_context).redline
    assert degraded.confidence == 0.42
    assert degraded.status is ReviewStatus.PENDING_REVIEW


# ------------------------------------------------------------------ untrusted LLM output


class _StubProvider:
    """A provider that returns whatever it was handed. Stands in for a live model."""

    def __init__(self, payload: dict):
        self._payload = payload

    def generate(self, request):
        return self._payload


def _draft(**overrides) -> dict:
    return {"suggested_text": "x", "rationale": "y", "confidence": 0.9} | overrides


@pytest.mark.parametrize("returned", [5.0, -3.0])
def test_a_confidence_outside_the_unit_range_is_clamped(sample_risks, sample_playbook,
                                                         retrieval_context, returned):
    """A live model can return any float it likes; the gate only means something in [0, 1].

    The mock provider self-clamps, so nothing outside `LLM_MODE=live` exercises this. The
    upper bound was previously an accident of `min_ocr_confidence` defaulting to 1.0 and
    the lower bound was unguarded entirely — a negative confidence reached `Redline`,
    which declares a bare `float`.
    """
    risk = _risk(sample_risks, "RISK-DOC-001-001")
    outcome = generate(risk, _rule(sample_playbook, risk), retrieval_context,
                       _StubProvider(_draft(confidence=returned)))
    assert outcome.redline is not None
    assert 0.0 <= outcome.redline.confidence <= 1.0
    # The §7 cap still applies on top of the clamp.
    worst = min(
        c.ocr_confidence for c in retrieval_context.chunks
        if c.chunk_id in outcome.redline.grounding_chunk_ids
    )
    assert outcome.redline.confidence <= worst


def test_a_malformed_draft_escalates_rather_than_crashing(sample_risks, sample_playbook,
                                                           retrieval_context):
    """A missing key is a structural failure: no redline, one queue item, no KeyError."""
    risk = _risk(sample_risks, "RISK-DOC-001-001")
    payload = _draft()
    del payload["suggested_text"]

    outcome = generate(risk, _rule(sample_playbook, risk), retrieval_context,
                       _StubProvider(payload))
    assert outcome.redline is None
    assert outcome.escalation is not None


# ------------------------------------------------------------------ degradation


def test_a_rule_the_playbook_does_not_have_is_skipped_not_fatal(sample_risks, sample_playbook,
                                                                 retrieval_context):
    """One unmatched flag used to kill generation for every flag after it.

    An absent rule is a data problem, not an ambiguity a reviewer can resolve, so it is
    skipped and reported rather than escalated.
    """
    orphan = _risk(sample_risks, "RISK-DOC-001-001")
    trimmed = [r for r in sample_playbook if (r.rule_id, r.version) != (orphan.rule_id,
                                                                       orphan.rule_version)]
    result = run(sample_risks, trimmed, retrieval_context, MockRedlineProvider())

    assert [r.id for r in result.rule_not_found] == [orphan.id]
    assert orphan.id not in {r.risk_id for r in result.results}
    # Everything the playbook still covers is generated as usual.
    assert result.results


def test_zero_budget_escalates_instead_of_serving_a_seed_only_redline(sample_risks,
                                                                      sample_playbook,
                                                                      retrieval_context,
                                                                      monkeypatch):
    """§7's queue-everything failure mode, which the README documents and the code inverted.

    `range(1, 1)` is empty, so a zero budget skipped the loop entirely — but the grounding
    check after it still ran on the seed state. The sample fixtures all have a real gap in
    their seed, which hides this; the case that bites is a flag the seed alone satisfies,
    where zero retrieval produced the *most* confident output the mock provider can emit.
    """
    monkeypatch.setattr("app.redline.generator.assess", lambda *_: [])
    risk = _risk(sample_risks, "RISK-DOC-001-001")
    outcome = generate(risk, _rule(sample_playbook, risk), retrieval_context, budget=0)

    assert outcome.redline is None
    assert outcome.escalation[1].reason is EscalationReason.BUDGET_EXHAUSTED
    assert outcome.escalation[1].rounds_attempted == 0


# ------------------------------------------------------------------ determinism


def test_the_run_is_reproducible(sample_risks, sample_playbook, retrieval_context):
    """Mock mode is what the published fixtures are generated from; it cannot wobble."""
    first = run(sample_risks, sample_playbook, retrieval_context, MockRedlineProvider())
    second = run(sample_risks, sample_playbook, retrieval_context, MockRedlineProvider())
    assert [r.model_dump() for r in first.redlines] == [r.model_dump() for r in second.redlines]
