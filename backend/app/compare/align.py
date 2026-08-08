"""Pairing clauses across two documents.

Matching on `clause_ref` alone only works when both documents use the same numbering.
That is false for two unrelated contracts, and unreliable between versions of one — a
clause inserted at 3.1 renumbers everything after it, so v1's 4.2 is v2's 4.3 and a
ref-equality match would pair two unrelated clauses with total confidence.

So: refs are strong evidence, not proof, and text similarity is the fallback.

There is no semantic/embedding fallback beyond that — consistent with the rest of this
codebase, which does not read `Chunk.embedding` anywhere yet (see
`app.ambiguity.strategies.ResolutionContext.widened_window` for the same span-based-not-
embedding-based choice made for the same reason). A clause that both **moved position**
and was **reworded enough to drop its `score_pair` below `align_match_threshold`** will not
be paired here: it comes back as one `removed` clause and one unrelated `added` clause,
not `modified`. A clause that moved but kept close to its original wording is still caught
correctly, by Pass 2 below, and reported as `"renumbered"` by `app.compare.service`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.compare.assemble import AssembledClause
from app.config import settings

_WHITESPACE = re.compile(r"\s+")
# Money, plain counts and percentages.
_NUMERIC = re.compile(r"[$£€]?\d[\d,]*(?:\.\d+)?%?")
_DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")


def normalize(text: str, heading: str = "") -> str:
    """Strip a clause down to the part that identifies it.

    Masking numbers is the trick that makes this work. The liability clause of v1 says
    `$1,000,000` and of v2 says `$750,000`; unmasked they score around 0.85 and risk
    falling under the match threshold, which would report them as one clause deleted and
    an unrelated one added — losing precisely the change you wanted to see. Masked, they
    align at ~0.98 and the amount shows up where it belongs, in the diff and the fact
    delta.
    """
    body = text[len(heading):] if heading and text.startswith(heading) else text
    body = _DATE.sub("#", body)
    body = _NUMERIC.sub("#", body)
    return _WHITESPACE.sub(" ", body.lower()).strip()


@dataclass(frozen=True)
class Pairing:
    left_ref: str | None
    right_ref: str | None
    score: float
    similarity: float


def _similarity(left: AssembledClause, right: AssembledClause) -> float:
    return SequenceMatcher(
        None, normalize(left.text, left.heading), normalize(right.text, right.heading),
        autojunk=False,
    ).ratio()


def score_pair(left: AssembledClause, right: AssembledClause) -> tuple[float, float]:
    """Return `(score, body_similarity)`.

    Weighted so that body text dominates (0.45) but a matching ref still breaks ties
    (0.35) and a matching heading contributes (0.20). Body similarity is returned
    separately because it is the number worth showing a human — the composite score is
    an implementation detail of the pairing.
    """
    ref = 1.0 if left.clause_ref == right.clause_ref else 0.0
    heading = (
        SequenceMatcher(None, left.heading.lower(), right.heading.lower()).ratio()
        if left.heading and right.heading
        else 0.0
    )
    body = _similarity(left, right)
    return 0.35 * ref + 0.20 * heading + 0.45 * body, body


def align_clauses(
    left: dict[str, AssembledClause], right: dict[str, AssembledClause]
) -> list[Pairing]:
    """Pair up two documents' clauses. Unpaired clauses come back with one side None."""
    unmatched_left = dict(left)
    unmatched_right = dict(right)
    pairings: list[Pairing] = []

    # Pass 1 — identical refs, but only when the content agrees enough to believe them.
    # `align_ref_trust_floor` is the weight of the ref term alone: scoring at exactly that
    # means the refs match and nothing else does, which is a renumbering coincidence.
    for ref in sorted(set(left) & set(right)):
        score, similarity = score_pair(left[ref], right[ref])
        if score >= settings.align_ref_trust_floor:
            pairings.append(Pairing(ref, ref, round(score, 4), round(similarity, 4)))
            unmatched_left.pop(ref, None)
            unmatched_right.pop(ref, None)

    # Pass 2 — everything else, by text. Blocked on section_type and prefiltered with
    # SequenceMatcher's cheap upper bounds: `real_quick_ratio` and `quick_ratio` are O(n)
    # where `ratio` is O(n²), and on 80x80 clause pairs of 1-3 KB each the difference is
    # seconds per request versus milliseconds.
    candidates: list[tuple[float, float, str, str]] = []
    for left_ref, left_clause in unmatched_left.items():
        left_norm = normalize(left_clause.text, left_clause.heading)
        matcher = SequenceMatcher(None, left_norm, "", autojunk=False)
        for right_ref, right_clause in unmatched_right.items():
            if left_clause.section_type != right_clause.section_type:
                continue
            matcher.set_seq2(normalize(right_clause.text, right_clause.heading))
            if matcher.real_quick_ratio() < settings.align_prefilter:
                continue
            if matcher.quick_ratio() < settings.align_prefilter:
                continue
            score, similarity = score_pair(left_clause, right_clause)
            if score >= settings.align_match_threshold:
                candidates.append((score, similarity, left_ref, right_ref))

    # Greedy on the sorted candidate list rather than Hungarian: optimal assignment needs
    # scipy (not a dependency) or ~80 lines, and on a matrix this sparse the two differ
    # only on genuinely ambiguous pairs — which surface anyway, as a low score the UI
    # marks as a guess.
    for score, similarity, left_ref, right_ref in sorted(candidates, reverse=True):
        if left_ref in unmatched_left and right_ref in unmatched_right:
            pairings.append(Pairing(left_ref, right_ref, round(score, 4), round(similarity, 4)))
            unmatched_left.pop(left_ref)
            unmatched_right.pop(right_ref)

    pairings.extend(Pairing(ref, None, 0.0, 0.0) for ref in sorted(unmatched_left))
    pairings.extend(Pairing(None, ref, 0.0, 0.0) for ref in sorted(unmatched_right))
    return pairings
