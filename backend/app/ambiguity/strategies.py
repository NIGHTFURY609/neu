"""The three resolution strategies, cheapest first.

Each one targets a *different* place the disambiguating information could be, which is
why running them in sequence is worth more than running any one of them harder:

  1. kg_precedent    - elsewhere in this document, already settled
  2. widen_context   - adjacent clauses and the definitions section
  3. alternate_parse - the clause that was cross-referenced

Every strategy returns a `StrategyOutcome` whose `summary` is written verbatim into the
retry trace, so it has to read as an explanation to a human reviewer, not a debug string.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.extraction.provider import CONTEXT_MARKER, LLMProvider
from app.schemas import CandidateEdge, Chunk, EdgeType, KGEdge


@dataclass
class ResolutionContext:
    """Everything the strategies are allowed to look at.

    Deliberately plain data: the retry loop never touches the database, so it can be
    unit-tested without one. The pipeline loads confirmed edges in and writes results
    back out.
    """

    chunks: list[Chunk]
    provider: LLMProvider
    confirmed_edges: list[KGEdge] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._ordered = sorted(self.chunks, key=lambda c: c.char_start)
        self._by_ref = {c.clause_ref: c for c in self.chunks}

    def chunk_for(self, clause_ref: str) -> Chunk | None:
        return self._by_ref.get(clause_ref)

    def widened_window(self, chunk: Chunk) -> list[Chunk]:
        """Immediately adjacent clauses plus the definitions section.

        Span-based rather than embedding-based on purpose — it keeps this stage
        independent of Dev 2's embedding model choice, and adjacency is the actual
        relationship we want here anyway.
        """
        index = self._ordered.index(chunk)
        neighbours = self._ordered[max(index - 1, 0) : index] + self._ordered[index + 1 : index + 2]
        definitions = [c for c in self._ordered if c.section_type == "definitions"]

        window: list[Chunk] = []
        for candidate in neighbours + definitions:
            if candidate.chunk_id != chunk.chunk_id and candidate not in window:
                window.append(candidate)
        return window


@dataclass(frozen=True)
class StrategyOutcome:
    resolved: bool
    summary: str
    edge_type: EdgeType | None = None
    confidence: float = 0.0


def _unresolved(summary: str) -> StrategyOutcome:
    return StrategyOutcome(resolved=False, summary=summary)


def _capped(confidence: float, *read: Chunk) -> float:
    """A resolution can never be more trustworthy than the pages it was read off.

    §7 — the same cap `clause_ner` applies to extracted facts, over every chunk the
    strategy actually looked at. It has to happen *before* the threshold gate, or a
    strategy confirms an edge at a confidence it doesn't hold.
    """
    return round(min([confidence, *(c.ocr_confidence for c in read)]), 4)


def _cap_note(raw: float, capped: float) -> str:
    """Names the cap in the trace when it actually bit.

    Without this the trace reads '0.8 ... below the 0.75 threshold', which looks like a
    bug to the reviewer rather than what it is: a scan we could not read well enough.
    """
    return "" if capped >= raw else f" (scored {raw}, capped to {capped} by OCR quality)"


def kg_precedent(candidate: CandidateEdge, ctx: ResolutionContext) -> StrategyOutcome:
    """Has this same relationship shape already been resolved in this document?

    Cheapest and most auditable of the three — no model call, and the answer points at a
    specific confirmed edge a reviewer can go read.
    """
    if not candidate.pattern_key:
        return _unresolved("No drafting pattern recorded for this candidate; no precedent to match.")

    matches = [
        edge
        for edge in ctx.confirmed_edges
        if edge.pattern_key == candidate.pattern_key and edge.edge_type in candidate.candidate_types
    ]
    if not matches:
        return _unresolved(
            f"No confirmed edge in this document matches the drafting pattern "
            f"'{candidate.pattern_key}'."
        )

    precedent = max(matches, key=lambda e: e.confidence)
    confidence = round((candidate.confidence + precedent.confidence) / 2, 4)
    detail = (
        f"Section {precedent.src_clause_ref} -> Section {precedent.dst_clause_ref} "
        f"({precedent.edge_type.value})"
    )
    if confidence < settings.resolve_confidence:
        return _unresolved(
            f"Found precedent {detail}, but combined confidence {confidence} is below the "
            f"{settings.resolve_confidence} threshold."
        )
    return StrategyOutcome(
        resolved=True,
        summary=f"Applied precedent {detail}, already confirmed for the same drafting pattern.",
        edge_type=precedent.edge_type,
        confidence=confidence,
    )


def widen_context(candidate: CandidateEdge, ctx: ResolutionContext) -> StrategyOutcome:
    """Re-extract with the adjacent clauses and definitions section in view.

    The disambiguating language is very often a defined term sitting just outside the
    original chunk window.
    """
    chunk = ctx.chunk_for(candidate.src_clause_ref)
    if chunk is None:
        return _unresolved(f"No chunk found for Section {candidate.src_clause_ref}.")

    window = ctx.widened_window(chunk)
    if not window:
        return _unresolved("No adjacent or definitional context available to widen into.")

    refs = ", ".join(f"Section {c.clause_ref}" for c in window)
    context = "\n\n".join(c.text for c in window)
    widened = chunk.model_copy(update={"text": f"{chunk.text}\n\n{CONTEXT_MARKER}\n\n{context}"})

    for raw in ctx.provider.extract_edges(widened):
        if raw["dst_clause_ref"] != candidate.dst_clause_ref:
            continue
        types = [EdgeType(t) for t in raw["candidate_types"]]
        if len(types) != 1:
            return _unresolved(
                f"Widened to {refs}; still reads as "
                f"{' or '.join(t.value for t in types)}."
            )
        confidence = _capped(raw["confidence"], chunk, *window)
        if confidence < settings.resolve_confidence:
            return _unresolved(
                f"Widened to {refs}; settled on {types[0].value} but only at "
                f"{confidence} confidence{_cap_note(raw['confidence'], confidence)}."
            )
        return StrategyOutcome(
            resolved=True,
            summary=f"Widened to {refs}; the added context resolves this as {types[0].value}.",
            edge_type=types[0],
            confidence=confidence,
        )

    return _unresolved(f"Widened to {refs}; the relationship no longer appears at all.")


def alternate_parse(candidate: CandidateEdge, ctx: ResolutionContext) -> StrategyOutcome:
    """Test each reading head-to-head against the clause it cross-references.

    Where widen_context looks *outward* at neighbours, this looks at the specific clause
    on the other end of the edge — the text the relationship is actually about.
    """
    chunk = ctx.chunk_for(candidate.src_clause_ref)
    if chunk is None:
        return _unresolved(f"No chunk found for Section {candidate.src_clause_ref}.")

    cross = ctx.chunk_for(candidate.dst_clause_ref)
    if cross is None:
        return _unresolved(
            f"Cross-referenced Section {candidate.dst_clause_ref} is not present in this document."
        )

    scores = ctx.provider.score_parses(chunk, cross.text, candidate.candidate_types)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (top_type, raw_top) = ranked[0]
    top_score = _capped(raw_top, chunk, cross)
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    detail = ", ".join(f"{name} {score}" for name, score in ranked)

    if top_score < settings.resolve_confidence:
        return _unresolved(
            f"Scored against Section {cross.clause_ref} ({detail}); best reading is below the "
            f"{settings.resolve_confidence} threshold{_cap_note(raw_top, top_score)}."
        )
    # Margin compares the raw scores: how far apart the two readings are is a property of
    # the language, not of how cleanly the page scanned.
    if raw_top - runner_up < settings.alternate_parse_margin:
        return _unresolved(
            f"Scored against Section {cross.clause_ref} ({detail}); readings are too close "
            f"to separate."
        )
    return StrategyOutcome(
        resolved=True,
        summary=f"Scored against Section {cross.clause_ref} ({detail}); {top_type} is the "
        f"only reading the language supports.",
        edge_type=EdgeType(top_type),
        confidence=top_score,
    )


# Order matters: no model call, then one cheap re-extraction, then a head-to-head compare.
STRATEGIES = [
    ("kg_precedent", kg_precedent),
    ("widen_context", widen_context),
    ("alternate_parse", alternate_parse),
]
