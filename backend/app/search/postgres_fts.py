"""Postgres full-text search over `chunks.search_tsv` (revision 0005).

The production backend. Every filter this needs — RBAC tags, doc_kind, jurisdiction,
document ids — is a column in the database, so filtering happens there rather than
pulling a candidate set into Python to score it.

Ranking is `ts_rank_cd`: cover density over weighted lexemes, with no IDF term. It is not
Okapi BM25 (see `memory_bm25.py` for that), and on a corpus of tens of documents the
difference is not observable — but the name of this class says what it actually does.
"""

from __future__ import annotations

from sqlalchemy import case, desc, func, literal, or_, select
from sqlalchemy.orm import Session

from app import models
from app.auth.rbac import visible_documents
from app.search.backend import SearchHit
from app.search.query import parse_query

# Definitions clauses are boosted at query time rather than at index time, so the boost
# can be tuned without rewriting the table.
_DEFINITION_BOOST = 1.15
# An exact clause-number match should beat prose relevance outright.
_REF_BONUS = 0.5


class PostgresFTSBackend:
    name = "postgres_fts"

    def __init__(self, session: Session) -> None:
        self._session = session

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
        if not parsed.raw:
            return []

        # websearch_to_tsquery, not plainto_: it understands quoted phrases, OR and -,
        # and — unlike to_tsquery — never raises on punctuation a user typed.
        tsquery = func.websearch_to_tsquery("english", literal(parsed.raw))

        # Normalization flag 32 maps rank to rank/(rank+1), bounding it in 0..1 so
        # `SearchHit.score` means the same thing here as in the other backend.
        rank = func.ts_rank_cd(models.Chunk.search_tsv, tsquery, 32)
        boost = case((models.Chunk.section_type == "definitions", _DEFINITION_BOOST), else_=1.0)
        ref_hit = (
            models.Chunk.clause_ref.in_(parsed.clause_refs)
            if parsed.clause_refs
            else literal(False)
        )
        score = (rank * boost + case((ref_hit, _REF_BONUS), else_=0.0)).label("score")

        inner = (
            select(
                models.Chunk.chunk_id,
                models.Chunk.document_id,
                models.Chunk.text,
                models.Chunk.page,
                models.Chunk.clause_ref,
                models.Chunk.section_type,
                models.Document.filename,
                models.Document.doc_kind,
                models.Document.jurisdiction,
                score,
            )
            .join(models.Document, models.Document.document_id == models.Chunk.document_id)
            .where(or_(models.Chunk.search_tsv.op("@@")(tsquery), ref_hit))
        )
        if principal is not None:
            inner = inner.where(visible_documents(principal))
        if doc_kind is not None:
            inner = inner.where(models.Document.doc_kind == doc_kind)
        if document_ids:
            inner = inner.where(models.Chunk.document_id.in_(document_ids))
        if jurisdiction is not None:
            inner = inner.where(models.Document.jurisdiction == jurisdiction)

        subquery = inner.order_by(desc("score")).limit(limit).subquery()

        # ts_headline runs in an outer select over the already-LIMITed rows. Running it
        # inside the ranked query would compute a headline for every candidate row in the
        # corpus and throw almost all of them away — the classic FTS performance mistake.
        # Empty StartSel/StopSel means the snippet comes back as plain text; the client
        # highlights from `terms`.
        stmt = select(
            subquery,
            func.ts_headline(
                "english",
                subquery.c.text,
                tsquery,
                "StartSel=,StopSel=,MaxWords=38,MinWords=18,MaxFragments=1",
            ).label("snippet"),
        )

        return [
            SearchHit(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                filename=row.filename or "",
                doc_kind=row.doc_kind or "contract",
                clause_ref=row.clause_ref or "",
                section_type=row.section_type or "clause",
                page=row.page or 0,
                text=row.text or "",
                snippet=(row.snippet or "").strip(),
                score=round(min(float(row.score or 0.0), 1.0), 4),
                jurisdiction=row.jurisdiction,
                backend=self.name,
            )
            for row in self._session.execute(stmt)
        ]
