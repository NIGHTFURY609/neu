"""Parsing what a user typed into what the backends need."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "2.2", "4.1.3" — a whole query that is just a clause number.
_BARE_REF = re.compile(r"^\d+(?:\.\d+)*$")
# "Section 4.1" anywhere in the query.
_SECTION_REF = re.compile(r"\bSection\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
# Quoted defined terms: `"Excluded Claims"`.
_QUOTED = re.compile(r'"([^"]+)"')
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")

_STOPWORDS = {"the", "and", "for", "with", "that", "this", "any", "all", "shall", "such"}


@dataclass
class SearchQuery:
    raw: str
    clause_refs: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)


def parse_query(raw: str) -> SearchQuery:
    """Pull clause references and quoted phrases out of a free-text query.

    Clause refs are extracted so the backends can OR a direct equality against the
    already-indexed `chunks.clause_ref` column. Postgres' default parser does keep "4.1"
    whole (it classifies it as a float), so text search alone would usually work — but
    "usually" is not good enough for the one lookup a lawyer does constantly, and an
    indexed equality is both exact and cheaper.
    """
    stripped = raw.strip()
    refs: list[str] = []
    if _BARE_REF.match(stripped):
        refs.append(stripped)
    refs.extend(match.group(1) for match in _SECTION_REF.finditer(stripped))

    phrases = [m.group(1) for m in _QUOTED.finditer(stripped)]
    terms = [
        word.lower()
        for word in _WORD.findall(stripped)
        if word.lower() not in _STOPWORDS and len(word) > 2
    ]
    return SearchQuery(
        raw=stripped,
        clause_refs=sorted(set(refs)),
        phrases=phrases,
        terms=sorted(set(terms)),
    )
