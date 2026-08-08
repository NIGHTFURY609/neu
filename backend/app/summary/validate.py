"""Enforcing that every claim resolves to a row that exists.

This is the anti-hallucination mechanism, and it is deliberately a lookup rather than a
line in the system prompt. `SUMMARY_SYSTEM` asks the model not to invent citations;
this makes it not matter whether it complied.
"""

from __future__ import annotations

from app.summary.schemas import Citation, Claim, ContractSummary, RiskHighlight

_SINGLE_CLAIM_FIELDS = ("term", "payment", "liability_cap", "termination", "governing_law")
_LIST_CLAIM_FIELDS = ("parties", "key_obligations", "unusual_clauses")


def _filter_claim(claim: Claim, citable: frozenset[str], dropped: list[str]) -> Claim | None:
    kept = [source for source in claim.sources if source.ref in citable]
    if len(kept) != len(claim.sources):
        missing = sorted({s.ref for s in claim.sources} - citable)
        dropped.append(
            f'a generated statement cited {", ".join(missing)}, which '
            f"{'do' if len(missing) > 1 else 'does'} not exist in this document"
        )
    if not kept:
        return None
    return Claim(text=claim.text, sources=kept)


def enforce_citations(summary: ContractSummary, citable: frozenset[str]) -> ContractSummary:
    """Drop unresolvable citations, then drop claims left with none.

    Every drop is recorded in `coverage_notes` rather than silently discarded. A summary
    that quietly loses a section reads as a contract with nothing to say about it, which
    is the opposite of the truth.
    """
    dropped: list[str] = []

    for field in _SINGLE_CLAIM_FIELDS:
        claim = getattr(summary, field)
        if claim is not None:
            setattr(summary, field, _filter_claim(claim, citable, dropped))

    for field in _LIST_CLAIM_FIELDS:
        claims = getattr(summary, field)
        setattr(
            summary,
            field,
            [c for c in (_filter_claim(x, citable, dropped) for x in claims) if c is not None],
        )

    kept_risks: list[RiskHighlight] = []
    for highlight in summary.top_risks:
        # A risk highlight names a risk_id directly, so it is checked against the store
        # the same way a citation is — a highlight for a finding that does not exist is
        # exactly the failure mode this guards.
        if highlight.risk_id not in citable:
            dropped.append(f"a generated risk highlight named {highlight.risk_id}, which does not exist")
            continue
        statement = _filter_claim(highlight.statement, citable, dropped)
        if statement is None:
            continue
        highlight.statement = statement
        kept_risks.append(highlight)
    summary.top_risks = kept_risks

    summary.coverage_notes = list(summary.coverage_notes) + dropped
    return summary


def citable_ids(*, clauses, facts, risks, edges, redlines) -> frozenset[str]:
    """Every id a claim may legitimately cite."""
    ids: set[str] = set(clauses)
    ids.update(f.fact_id for f in facts)
    ids.update(f.clause_ref for f in facts)
    ids.update(r.id for r in risks)
    ids.update(r.clause_ref for r in risks)
    ids.update(e.edge_id for e in edges)
    ids.update(r.redline_id for r in redlines)
    ids.discard("")
    return frozenset(ids)


def coverage_notes(*, clauses, edges_pending: int, worst_ocr: float) -> list[str]:
    """What the summary could not see. Computed, never generated."""
    from app.compare.assemble import PREAMBLE_REF

    notes: list[str] = []
    preamble = clauses.get(PREAMBLE_REF)
    if preamble is not None:
        notes.append(
            f"{len(preamble.chunk_ids)} chunk(s) carry no clause number and are summarized "
            "only as a block; statements below do not cover them individually."
        )
    if edges_pending:
        notes.append(
            f"{edges_pending} clause relationship(s) are still awaiting human review and were "
            "treated as absent, per ARCHITECTURE.md §3.3."
        )
    if worst_ocr < 0.75:
        notes.append(
            f"Worst page OCR confidence is {worst_ocr:.2f}; anything derived from that page "
            "is capped accordingly and may be misread."
        )
    return notes
