"""Reassembling whole clauses from the chunks a document was split into.

Everything downstream of ingestion keys off `clause_ref`, but a clause is not a chunk:
`ingestion/chunker.py` splits at 800 characters with 150 of overlap, so a clause of any
length spans several. `app/redline/retrieval.py` builds `{c.clause_ref: c for c in chunks}`
and therefore keeps only the *last* chunk of each clause — which is why a redline's
`original_text` can be the tail of a clause rather than the clause.

This module is the fix for the consumers written after it. It deliberately does not touch
`RetrievalContext`: changing what the redline generator considers a clause would move
every published redline fixture, which is a separate change with its own blast radius.

Three properties of the chunk data shape this:

- **Spans are page-relative.** `chunker` restarts offsets on each page, so `char_start`
  from page 2 is not comparable with `char_end` from page 1. Stitching therefore only
  ever does arithmetic *within* a page and joins across pages with a newline.
- **Chunks before the first heading carry `clause_ref = ""`.** Downstream they are
  invisible rather than merely unlabelled. Here they are surfaced under a synthetic ref
  so a summary can at least report that they exist.
- **`chunk_index` is not persisted to Postgres.** Order is recovered from
  `(page, char_start)`, which is correct because spans are monotonic within a page.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.schemas import Chunk

# Same shape `ingestion/clause_labeler.py` uses to assign refs in the first place.
CLAUSE_HEADER = re.compile(
    r"^\s*(\d+(?:\.\d+)*)\.?\s+([A-Z][A-Za-z ,/&\-]{2,60})\.?\s*$", re.MULTILINE
)

# Chunks that never got a clause number are collected here rather than dropped. The
# parenthesis keeps it from ever colliding with a real ref, which are digits and dots.
PREAMBLE_REF = "(preamble)"


@dataclass(frozen=True)
class AssembledClause:
    clause_ref: str
    heading: str
    text: str
    section_type: str
    pages: tuple[int, int]
    chunk_ids: tuple[str, ...]
    min_ocr_confidence: float

    @property
    def is_preamble(self) -> bool:
        return self.clause_ref == PREAMBLE_REF


def _heading_of(text: str) -> str:
    match = CLAUSE_HEADER.search(text)
    return match.group(0).strip() if match else ""


def _stitch(ordered: list[Chunk]) -> str:
    """Join chunks back into one string, removing the chunker's overlap.

    Within a page the overlap is exact and recoverable: consecutive chunks were cut with
    `start = end - overlap`, so when `b.char_start < a.char_end` the first
    `a.char_end - b.char_start` characters of `b` are a repeat of the tail of `a`.
    Across a page boundary the spans mean different things, so no arithmetic is done and
    the two are simply joined.
    """
    if not ordered:
        return ""

    parts = [ordered[0].text]
    for previous, current in zip(ordered, ordered[1:]):
        if current.page != previous.page:
            parts.append("\n")
            parts.append(current.text)
            continue
        overlap = previous.char_end - current.char_start
        if 0 < overlap <= len(current.text):
            parts.append(current.text[overlap:])
        elif overlap <= 0:
            parts.append(" " if not parts[-1].endswith((" ", "\n")) else "")
            parts.append(current.text)
        # overlap > len(current.text) means this chunk is wholly contained in the
        # previous one; contributing nothing is correct.
    return "".join(parts).strip()


def assemble_clauses(chunks: list[Chunk]) -> dict[str, AssembledClause]:
    """Group chunks into whole clauses, keyed by `clause_ref`.

    Unlabelled chunks are returned under `PREAMBLE_REF` rather than dropped, so a caller
    can report them. Ordering within a clause is `(page, char_start)`.
    """
    buckets: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        ref = chunk.clause_ref.strip() if chunk.clause_ref else ""
        buckets.setdefault(ref or PREAMBLE_REF, []).append(chunk)

    assembled: dict[str, AssembledClause] = {}
    for ref, group in buckets.items():
        ordered = sorted(group, key=lambda c: (c.page, c.char_start, c.chunk_id))
        pages = [c.page for c in ordered]
        # Majority vote, because a clause whose first chunk contains a `"X" means`
        # definition is labelled `definitions` while its continuation chunks are not.
        section_type = Counter(c.section_type for c in ordered).most_common(1)[0][0]
        assembled[ref] = AssembledClause(
            clause_ref=ref,
            heading="" if ref == PREAMBLE_REF else _heading_of(ordered[0].text),
            text=_stitch(ordered),
            section_type=section_type,
            pages=(min(pages), max(pages)),
            chunk_ids=tuple(c.chunk_id for c in ordered),
            # The worst page wins, matching how confidence is capped everywhere else:
            # a clause is only as trustworthy as the worst scan it was read from.
            min_ocr_confidence=min(c.ocr_confidence for c in ordered),
        )
    return assembled
