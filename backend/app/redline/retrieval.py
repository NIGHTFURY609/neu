"""What the active retrieval loop is allowed to look at, and what it has looked at.

Two objects, kept apart on purpose:

  `RetrievalContext` - the stores, read-only. Vector DB chunks, *confirmed* KG edges,
                       extracted facts. The confirmed-only rule (§3.3) is enforced at
                       construction, so no query below can accidentally read an
                       unconfirmed relationship.
  `RetrievalState`   - what this run has actually pulled back so far. Grounding is
                       assessed against this, and it is what ends up in the redline's
                       `grounding_chunk_ids`.

Plain data, no database — same as `ambiguity.strategies.ResolutionContext`, and for the
same reason: the loop is the part worth unit-testing, and it should not need Postgres.

Retrieval is span- and term-based rather than embedding-based. Nothing here reads
`Chunk.embedding`, so Dev 2's choice of embedding model does not block or change this
stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas import Chunk, Fact, KGEdge, ReviewStatus

# A clause cross-reference in contract prose: "Section 4.1", "Section 12".
SECTION_REF = re.compile(r"Section (\d+(?:\.\d+)*)")
# A term the definitions section defines: `"Excluded Claims" means ...`. Matching the
# definition rather than guessing at capitalisation keeps the glossary exact — contract
# prose is full of Title Case that is not a defined term.
DEFINED_TERM = re.compile(r'"([^"]+)"\s+means')


@dataclass
class RetrievalContext:
    """The stores the loop may query."""

    chunks: list[Chunk]
    confirmed_edges: list[KGEdge]
    facts: list[Fact] = field(default_factory=list)
    # Clause pairs joined by a `pending_review` edge. Deliberately *only* the refs: the
    # loop is allowed to know the Knowledge Graph has an unsettled relationship here —
    # that is why it cannot ground the clause — but never what the candidate reading
    # was, which is exactly what §3.3 forbids it from trusting.
    unresolved_pairs: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        unconfirmed = [e for e in self.confirmed_edges if e.status is not ReviewStatus.CONFIRMED]
        if unconfirmed:
            raise ValueError(
                f"RetrievalContext was given {len(unconfirmed)} non-confirmed edge(s): "
                f"{', '.join(e.edge_id for e in unconfirmed)}"
            )
        self._by_ref = {c.clause_ref: c for c in self.chunks}
        self._by_id = {f.fact_id: f for f in self.facts}
        self.glossary = {
            term: chunk
            for chunk in self.chunks
            if chunk.section_type == "definitions"
            for term in DEFINED_TERM.findall(chunk.text)
        }

    # ------------------------------------------------------------------- queries

    def clause(self, ref: str) -> Chunk | None:
        return self._by_ref.get(ref)

    def definition_of(self, term: str) -> Chunk | None:
        return self.glossary.get(term)

    def edges_touching(self, ref: str) -> list[KGEdge]:
        """Confirmed edges with `ref` at either end."""
        return [
            e
            for e in self.confirmed_edges
            if ref in (e.src_clause_ref, e.dst_clause_ref)
        ]

    def facts_for(self, fact_ids: list[str]) -> list[Fact]:
        return [self._by_id[i] for i in fact_ids if i in self._by_id]

    def clauses_naming(self, *refs: str) -> list[Chunk]:
        """Clauses whose text cross-references every one of `refs`.

        The last thing the loop tries when the Knowledge Graph has no answer: some
        *other* clause in the document may state the precedence in prose.
        """
        return [c for c in self.chunks if all(r in SECTION_REF.findall(c.text) for r in refs)]

    def unresolved_partners(self, ref: str) -> list[str]:
        return [b if a == ref else a for a, b in self.unresolved_pairs if ref in (a, b)]


@dataclass
class RetrievalState:
    """Everything this run has pulled back, in the order it arrived."""

    chunks: list[Chunk] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)

    def add_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Add whatever is new and return just that, so a round can report what it got."""
        seen = {c.chunk_id for c in self.chunks}
        fresh = [c for c in chunks if c.chunk_id not in seen]
        self.chunks.extend(fresh)
        return fresh

    def add_edges(self, edges: list[KGEdge]) -> None:
        seen = {e.edge_id for e in self.edges}
        self.edges.extend(e for e in edges if e.edge_id not in seen)

    @property
    def refs(self) -> set[str]:
        return {c.clause_ref for c in self.chunks}

    @property
    def text(self) -> str:
        return "\n\n".join(c.text for c in self.chunks)

    @property
    def min_ocr_confidence(self) -> float:
        """§7 — a redline can be no more trustworthy than the worst page it was read off."""
        return min((c.ocr_confidence for c in self.chunks), default=1.0)
