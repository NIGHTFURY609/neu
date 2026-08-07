"""The bounded retry loop — the behaviour this stage exists to add.

Two things are under test: that each strategy resolves the case it is meant to, and that
the trace produced is faithful. The trace is a published contract (Dev 1 renders it), so
a wrong trace is as much a bug as a wrong edge.
"""

import pytest

from app.ambiguity.resolver import resolve
from app.ambiguity.strategies import ResolutionContext
from app.extraction.provider import MockProvider
from app.schemas import CandidateEdge, EdgeType, KGEdge, Provenance, ReviewStatus


@pytest.fixture
def ctx(sample_chunks) -> ResolutionContext:
    return ResolutionContext(chunks=sample_chunks, provider=MockProvider())


def candidate(src, dst, types, confidence, pattern_key) -> CandidateEdge:
    return CandidateEdge(
        edge_id=f"E-{src}-{dst}",
        document_id="DOC-001",
        src_clause_ref=src,
        dst_clause_ref=dst,
        candidate_types=types,
        confidence=confidence,
        evidence_chunk_ids=[],
        pattern_key=pattern_key,
    )


def confirmed(src, dst, edge_type, confidence, pattern_key) -> KGEdge:
    return KGEdge(
        edge_id=f"C-{src}-{dst}",
        document_id="DOC-001",
        src_clause_ref=src,
        dst_clause_ref=dst,
        edge_type=edge_type,
        status=ReviewStatus.CONFIRMED,
        confidence=confidence,
        evidence_chunk_ids=[],
        pattern_key=pattern_key,
        resolved_by=Provenance.DIRECT_EXTRACTION,
    )


def test_round_one_resolves_from_precedent(ctx):
    """Section 5.1 uses the same drafting pattern 4.1 already established."""
    ctx.confirmed_edges.append(
        confirmed("4.1", "2.2", EdgeType.OVERRIDES, 0.94, "notwithstanding_shall_control")
    )
    result = resolve(
        candidate("5.1", "2.4", [EdgeType.OVERRIDES, EdgeType.MODIFIES], 0.58, "notwithstanding_shall_control"),
        ctx,
    )

    assert result.resolved
    assert result.edge_type is EdgeType.OVERRIDES
    assert result.resolved_by is Provenance.RETRY_KG_PRECEDENT
    assert result.rounds_attempted == 1
    assert result.trace[0].attempt == "kg_precedent"
    assert result.trace[0].resolved is True
    assert "4.1" in result.trace[0].result


def test_round_two_resolves_from_widened_context(ctx):
    """Section 7.3 needs the definition of Excluded Claims, which sits in Section 1.1."""
    result = resolve(
        candidate("7.3", "2.2", [EdgeType.WAIVES, EdgeType.MODIFIES], 0.55, "no_claim_in_respect_of"),
        ctx,
    )

    assert result.resolved
    assert result.edge_type is EdgeType.MODIFIES
    assert result.resolved_by is Provenance.RETRY_WIDEN_CONTEXT
    assert result.rounds_attempted == 2
    # Round 1 must be recorded even though it failed — that is the point of the trace.
    assert [r.resolved for r in result.trace] == [False, True]
    assert result.trace[0].attempt == "kg_precedent"
    assert result.trace[1].attempt == "widen_context"
    assert "Section 1.1" in result.trace[1].result


def test_round_three_resolves_from_the_cross_referenced_clause(ctx):
    """Section 6.1 is settled only by the release language inside Section 2.4 itself."""
    result = resolve(
        candidate("6.1", "2.4", [EdgeType.WAIVES, EdgeType.MODIFIES], 0.55, "no_claim_under_section"),
        ctx,
    )

    assert result.resolved
    assert result.edge_type is EdgeType.WAIVES
    assert result.resolved_by is Provenance.RETRY_ALTERNATE_PARSE
    assert result.rounds_attempted == 3
    assert [r.attempt for r in result.trace] == ["kg_precedent", "widen_context", "alternate_parse"]
    assert [r.resolved for r in result.trace] == [False, False, True]


def test_exhausted_budget_escalates_with_a_full_trace(ctx):
    """Section 8.2's ambiguity lives in the source text; no strategy can reach it."""
    result = resolve(
        candidate("8.2", "3.1", [EdgeType.OVERRIDES, EdgeType.WAIVES, EdgeType.MODIFIES], 0.41, "in_lieu_of"),
        ctx,
    )

    assert not result.resolved
    assert result.edge_type is None
    assert result.resolved_by is None
    assert result.rounds_attempted == 3
    assert all(r.resolved is False for r in result.trace)
    assert [r.round for r in result.trace] == [1, 2, 3]
    # Every round explains itself — a reviewer opening this in the queue can see what
    # was already tried without reading the code.
    assert all(len(r.result) > 20 for r in result.trace)


def test_budget_caps_the_number_of_rounds(ctx):
    """Latency guard: a single ambiguous edge cannot run more strategies than budgeted."""
    result = resolve(
        candidate("6.1", "2.4", [EdgeType.WAIVES, EdgeType.MODIFIES], 0.55, "no_claim_under_section"),
        ctx,
        budget=2,
    )

    assert not result.resolved
    assert result.rounds_attempted == 2


def test_zero_budget_escalates_immediately(ctx):
    """Budget 0 degrades to the original 3.2 behaviour: escalate every ambiguity."""
    result = resolve(
        candidate("7.3", "2.2", [EdgeType.WAIVES, EdgeType.MODIFIES], 0.55, "no_claim_in_respect_of"),
        ctx,
        budget=0,
    )

    assert not result.resolved
    assert result.rounds_attempted == 0
    assert result.trace == []


def test_precedent_ignores_edge_types_the_candidate_never_proposed(ctx):
    """A same-pattern precedent that resolved to a type not in play is not applicable."""
    ctx.confirmed_edges.append(
        confirmed("4.1", "2.2", EdgeType.DEPENDS_ON, 0.99, "notwithstanding_shall_control")
    )
    result = resolve(
        candidate("5.1", "2.4", [EdgeType.OVERRIDES, EdgeType.MODIFIES], 0.58, "notwithstanding_shall_control"),
        ctx,
        budget=1,
    )

    assert not result.resolved
    assert "No confirmed edge" in result.trace[0].result


def test_bad_ocr_blocks_a_resolution_that_would_otherwise_succeed(sample_chunks):
    """§7 — a resolution is never more trustworthy than the page it was read off.

    Section 6.1 normally resolves at round 3 with 0.8. Degrade the OCR on the pages the
    strategy reads and the same run must escalate instead of confirming at a confidence
    the stage does not actually hold.
    """
    degraded = [
        c.model_copy(update={"ocr_confidence": 0.62}) if c.clause_ref in {"6.1", "2.4"} else c
        for c in sample_chunks
    ]
    result = resolve(
        candidate("6.1", "2.4", [EdgeType.WAIVES, EdgeType.MODIFIES], 0.55, "no_claim_under_section"),
        ResolutionContext(chunks=degraded, provider=MockProvider()),
    )

    assert not result.resolved
    assert result.rounds_attempted == 3
    assert "below the" in result.trace[2].result


def test_low_confidence_precedent_does_not_resolve(ctx):
    """Matching the pattern is not enough; the combined confidence has to clear the bar."""
    ctx.confirmed_edges.append(
        confirmed("4.1", "2.2", EdgeType.OVERRIDES, 0.60, "notwithstanding_shall_control")
    )
    result = resolve(
        candidate("5.1", "2.4", [EdgeType.OVERRIDES, EdgeType.MODIFIES], 0.58, "notwithstanding_shall_control"),
        ctx,
        budget=1,
    )

    assert not result.resolved
    assert "below the" in result.trace[0].result
