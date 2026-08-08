"""Is the agent grounded yet, and if not, what exactly is missing?

This is the "evaluate" half of §4.1's query → evaluate → refine loop, and it is what
makes the loop *active* rather than a fixed top-k fetch: `assess` does not report a
score, it reports the specific gaps, each already expressed as the one targeted query
that would close it. The generator issues them highest-priority first.

`assess` returning `[]` is the definition of "sufficient grounding". Every gap kind
below is a thing a reviewer would ask about before signing off on a rewrite:

  1. a clause the flagged clause cross-references, that we have not read
  2. a capitalised term the retrieved text relies on, whose definition we have not read
  3. a confirmed KG edge bearing on the flagged clause, whose other end we have not read
  4. a relationship the Knowledge Graph has not settled — the one gap retrieval cannot
     close, because the answer is waiting in the Human Review Queue
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.schemas import PlaybookRule, RiskFlag

from .retrieval import SECTION_REF, RetrievalContext, RetrievalState


@dataclass(frozen=True)
class Query:
    """One targeted retrieval, and what it did when it ran.

    `description` and the string `run` returns are written verbatim into the retrieval
    trace as `attempt` and `result`, so both have to read as an explanation to a human
    reviewer rather than as a debug string.
    """

    key: str
    description: str
    run: Callable[[RetrievalContext, RetrievalState], str]


def _fetch_clause(ref: str) -> Callable[[RetrievalContext, RetrievalState], str]:
    def run(ctx: RetrievalContext, state: RetrievalState) -> str:
        chunk = ctx.clause(ref)
        if chunk is None:
            return f"Section {ref} is not present in this document."
        state.add_chunks([chunk])
        return f"Returned Section {ref} (page {chunk.page})."

    return run


def _fetch_definition(term: str) -> Callable[[RetrievalContext, RetrievalState], str]:
    def run(ctx: RetrievalContext, state: RetrievalState) -> str:
        chunk = ctx.definition_of(term)
        if chunk is None:
            return f"'{term}' is used but never defined in this document."
        state.add_chunks([chunk])
        return f"Returned the Section {chunk.clause_ref} definition of '{term}'."

    return run


def _fetch_related(ref: str) -> Callable[[RetrievalContext, RetrievalState], str]:
    def run(ctx: RetrievalContext, state: RetrievalState) -> str:
        edges = ctx.edges_touching(ref)
        if not edges:
            return f"No confirmed relationship in the Knowledge Graph touches Section {ref}."
        state.add_edges(edges)
        partners = sorted({e.dst_clause_ref if e.src_clause_ref == ref else e.src_clause_ref for e in edges})
        fresh = state.add_chunks([c for r in partners if (c := ctx.clause(r)) is not None])
        described = ", ".join(
            f"Section {e.src_clause_ref} {e.edge_type.value} Section {e.dst_clause_ref}" for e in edges
        )
        return (
            f"Returned {described}. "
            f"Pulled {', '.join(f'Section {c.clause_ref}' for c in fresh) or 'no new clause text'}."
        )

    return run


def _fetch_precedence_edge(ref: str, partner: str) -> Callable[[RetrievalContext, RetrievalState], str]:
    def run(ctx: RetrievalContext, state: RetrievalState) -> str:
        settled = [
            e
            for e in ctx.edges_touching(ref)
            if partner in (e.src_clause_ref, e.dst_clause_ref)
        ]
        if not settled:
            return (
                f"No confirmed edge settles Section {ref} against Section {partner}; the "
                f"only relationship the graph holds for this pair is still pending review."
            )
        state.add_edges(settled)
        return f"Returned {len(settled)} confirmed edge(s) for the pair."

    return run


def _fetch_precedence_prose(ref: str, partner: str) -> Callable[[RetrievalContext, RetrievalState], str]:
    def run(ctx: RetrievalContext, state: RetrievalState) -> str:
        candidates = [c for c in ctx.clauses_naming(ref, partner) if c.clause_ref != ref]
        if not candidates:
            return (
                f"No other clause names both Section {ref} and Section {partner}. The "
                f"order of precedence is unstated in the document, not missing from the "
                f"retrieval."
            )
        fresh = state.add_chunks(candidates)
        return f"Returned {', '.join(f'Section {c.clause_ref}' for c in fresh)}."

    return run


def assess(
    risk: RiskFlag,
    rule: PlaybookRule,
    ctx: RetrievalContext,
    state: RetrievalState,
) -> list[Query]:
    """Open grounding gaps, as the queries that would close them, most useful first."""
    gaps: list[Query] = []
    retrieved = state.refs

    # 1. Clauses the flagged clause points at. Scoped to the flagged clause on purpose —
    #    following every cross-reference of every retrieved clause walks the whole
    #    document and the budget is four rounds.
    flagged = ctx.clause(risk.clause_ref)
    if flagged is None:
        gaps.append(
            Query(
                key=f"clause:{risk.clause_ref}",
                description=f"Retrieve the flagged clause, Section {risk.clause_ref}",
                run=_fetch_clause(risk.clause_ref),
            )
        )
        return gaps
    for ref in sorted(set(SECTION_REF.findall(flagged.text)) - retrieved):
        gaps.append(
            Query(
                key=f"clause:{ref}",
                description=f"Retrieve Section {ref}, cross-referenced by the flagged clause",
                run=_fetch_clause(ref),
            )
        )

    # 2. Defined terms relied on by anything read so far, including the playbook's own
    #    standard language — a rewrite that introduces a term we have not grounded is
    #    exactly the hallucination §3.4 warns about.
    haystack = f"{state.text}\n{rule.standard_language or ''}"
    for term in sorted(ctx.glossary):
        definition = ctx.glossary[term]
        if term in haystack and definition.chunk_id not in {c.chunk_id for c in state.chunks}:
            gaps.append(
                Query(
                    key=f"definition:{term}",
                    description=f"Retrieve the definition of the capitalised term '{term}'",
                    run=_fetch_definition(term),
                )
            )

    # 3. Confirmed KG constraints on the flagged clause, and the clauses at their far end.
    related = ctx.edges_touching(risk.clause_ref)
    partners = {
        e.dst_clause_ref if e.src_clause_ref == risk.clause_ref else e.src_clause_ref
        for e in related
    }
    if related and partners - retrieved:
        gaps.append(
            Query(
                key=f"related:{risk.clause_ref}",
                description=(
                    f"Retrieve every clause the Knowledge Graph confirms bears on "
                    f"Section {risk.clause_ref}"
                ),
                run=_fetch_related(risk.clause_ref),
            )
        )

    # 4. The gap retrieval cannot close. A `pending_review` edge means the graph has an
    #    unsettled relationship here; §3.3 says treat it as absent, so the agent cannot
    #    use it — and it cannot rewrite the clause without knowing which one controls.
    #    Both attempts below exist so the trace shows the agent exhausted the document
    #    before deciding the ambiguity is in the source text.
    for partner in sorted(ctx.unresolved_partners(risk.clause_ref)):
        gaps.append(
            Query(
                key=f"precedence-edge:{risk.clause_ref}|{partner}",
                description=(
                    f"Retrieve a confirmed edge settling precedence between Section "
                    f"{risk.clause_ref} and Section {partner}"
                ),
                run=_fetch_precedence_edge(risk.clause_ref, partner),
            )
        )
        gaps.append(
            Query(
                key=f"precedence-prose:{risk.clause_ref}|{partner}",
                description=(
                    f"Retrieve any other clause stating which of Section "
                    f"{risk.clause_ref} and Section {partner} controls"
                ),
                run=_fetch_precedence_prose(risk.clause_ref, partner),
            )
        )

    return gaps
