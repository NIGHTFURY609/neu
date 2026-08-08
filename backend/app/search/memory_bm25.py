"""Okapi BM25 over an in-memory corpus.

This is the backend the tests use, and the only one they *can* use: no test in this repo
touches a real database, so a Postgres-only search layer would be entirely unverified.

It is also the honest home for the name. `PostgresFTSBackend` ranks with `ts_rank_cd`,
which is cover density without IDF; this is the real thing — term frequency saturating at
k1, length-normalized by b, weighted by inverse document frequency.

Not suitable for serving: it scores the whole candidate corpus per query, in-process,
with no shared index.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.search.backend import SearchHit
from app.search.query import parse_query

K1 = 1.2
B = 0.75
_TOKEN = re.compile(r"[a-z0-9][a-z0-9.'-]*")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class Doc:
    chunk_id: str
    document_id: str
    text: str
    clause_ref: str = ""
    section_type: str = "clause"
    page: int = 0
    filename: str = ""
    doc_kind: str = "contract"
    jurisdiction: str | None = None
    rbac_tags: tuple[str, ...] = ()
    owner_id: str | None = None


class InMemoryBM25Backend:
    name = "memory_bm25"

    def __init__(self, docs: list[Doc]) -> None:
        self._docs = docs
        self._tokens = {d.chunk_id: tokenize(d.text) for d in docs}
        self._freqs = {cid: Counter(toks) for cid, toks in self._tokens.items()}
        lengths = [len(t) for t in self._tokens.values()]
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0
        self._df: Counter[str] = Counter()
        for toks in self._tokens.values():
            self._df.update(set(toks))
        self._n = len(docs)

    @classmethod
    def from_session(cls, session) -> "InMemoryBM25Backend":
        from app import models
        from sqlalchemy import select

        stmt = select(models.Chunk, models.Document).join(
            models.Document, models.Document.document_id == models.Chunk.document_id
        )
        return cls(
            [
                Doc(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    clause_ref=chunk.clause_ref or "",
                    section_type=chunk.section_type,
                    page=chunk.page,
                    filename=document.filename,
                    doc_kind=document.doc_kind,
                    jurisdiction=document.jurisdiction,
                    rbac_tags=tuple(document.rbac_tags or []),
                    owner_id=document.owner_id,
                )
                for chunk, document in session.execute(stmt)
            ]
        )

    def _idf(self, term: str) -> float:
        # Robertson/Sparck-Jones with the +0.5 smoothing, floored at 0: a term in more
        # than half the corpus would otherwise score negative and actively demote the
        # documents containing it.
        df = self._df.get(term, 0)
        return max(0.0, math.log(1 + (self._n - df + 0.5) / (df + 0.5)))

    def _score(self, doc: Doc, terms: list[str]) -> float:
        freqs = self._freqs[doc.chunk_id]
        length = len(self._tokens[doc.chunk_id])
        norm = (1 - B + B * (length / self._avg_len)) if self._avg_len else 1.0
        total = 0.0
        for term in terms:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            total += self._idf(term) * (tf * (K1 + 1)) / (tf + K1 * norm)
        return total

    def search(
        self,
        query: str,
        *,
        principal=None,
        doc_kind: str | None = None,
        document_ids: list[str] | None = None,
        jurisdiction: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        parsed = parse_query(query)
        terms = parsed.terms + [t for p in parsed.phrases for t in tokenize(p)]
        terms += [r for r in parsed.clause_refs]
        if not terms:
            return []

        scored: list[tuple[float, Doc]] = []
        for doc in self._docs:
            if doc_kind is not None and doc.doc_kind != doc_kind:
                continue
            if document_ids and doc.document_id not in document_ids:
                continue
            if jurisdiction is not None and doc.jurisdiction != jurisdiction:
                continue
            if principal is not None and doc.owner_id != principal.user_id:
                continue
            score = self._score(doc, terms)
            if doc.clause_ref and doc.clause_ref in parsed.clause_refs:
                score += 5.0  # an exact clause-number hit outranks prose relevance
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda pair: (-pair[0], pair[1].chunk_id))
        top = scored[:limit]
        # Normalized against the best hit so `score` means the same 0..1 thing it does in
        # the other backend. This is relative-within-result-set, not an absolute quality.
        best = top[0][0] if top else 1.0
        return [
            SearchHit(
                chunk_id=doc.chunk_id,
                document_id=doc.document_id,
                filename=doc.filename,
                doc_kind=doc.doc_kind,
                clause_ref=doc.clause_ref,
                section_type=doc.section_type,
                page=doc.page,
                text=doc.text,
                snippet=_snippet(doc.text, terms),
                score=round(score / best, 4) if best else 0.0,
                jurisdiction=doc.jurisdiction,
                backend=self.name,
            )
            for score, doc in top
        ]


def _snippet(text: str, terms: list[str], window: int = 220) -> str:
    lowered = text.lower()
    positions = [lowered.find(t) for t in terms if lowered.find(t) >= 0]
    if not positions:
        return text[:window].strip()
    start = max(0, min(positions) - window // 3)
    end = min(len(text), start + window)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"
