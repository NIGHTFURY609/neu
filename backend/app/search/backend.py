"""The search contract every backend implements."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    filename: str = ""
    doc_kind: str = "contract"
    # "" for preamble chunks. Surfaced rather than hidden — a hit in unlabelled text is
    # still a hit, and pretending otherwise is how that text became invisible elsewhere.
    clause_ref: str = ""
    section_type: str = "clause"
    page: int = 0
    text: str = ""
    # Plain text, never markup. The client highlights using `SearchResponse.terms`, so no
    # HTML crosses the wire and a future vector backend — which has no tsquery to build a
    # headline from — produces the identical shape.
    snippet: str = ""
    # Always 0..1, whichever backend produced it.
    score: float = 0.0
    jurisdiction: str | None = None
    backend: str = ""


class SearchResponse(BaseModel):
    query: str
    terms: list[str] = Field(default_factory=list)
    total: int = 0
    hits: list[SearchHit] = Field(default_factory=list)
    backend: str = ""


class SearchBackend(Protocol):
    def search(
        self,
        query: str,
        *,
        principal,
        doc_kind: str | None = None,
        document_ids: list[str] | None = None,
        jurisdiction: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]: ...


def get_backend(session):
    from app.config import settings
    from app.search.postgres_fts import PostgresFTSBackend

    if settings.search_backend == "memory_bm25":
        # Only reachable by explicit configuration. The in-memory backend loads the
        # candidate corpus per query, which is fine for a test and wrong for a server.
        from app.search.memory_bm25 import InMemoryBM25Backend

        return InMemoryBM25Backend.from_session(session)
    return PostgresFTSBackend(session)
