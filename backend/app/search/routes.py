"""GET /search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.config import settings
from app.db import get_session
from app.search.backend import SearchResponse, get_backend
from app.search.query import parse_query

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=2, description='Free text. Supports "quoted phrases" and clause refs.'),
    doc_kind: str | None = Query(default=None, description="contract | regulation"),
    document_id: list[str] = Query(default=[]),
    jurisdiction: str | None = Query(default=None),
    limit: int = Query(default=0, le=100),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> SearchResponse:
    """Lexical search over document chunks.

    Results are RBAC-filtered in SQL, so a document you cannot open is not merely hidden
    from the list — it never reaches the ranking.

    `terms` comes back alongside the hits so the client can do its own highlighting. The
    snippet is plain text: no markup crosses the wire, which keeps the response free of
    anything a client would have to render as raw HTML.
    """
    backend = get_backend(session)
    parsed = parse_query(q)
    hits = backend.search(
        q,
        principal=principal,
        doc_kind=doc_kind,
        document_ids=list(document_id) or None,
        jurisdiction=jurisdiction,
        limit=limit or settings.search_default_limit,
    )
    return SearchResponse(
        query=parsed.raw,
        terms=parsed.terms + parsed.phrases + parsed.clause_refs,
        total=len(hits),
        hits=hits,
        backend=getattr(backend, "name", settings.search_backend),
    )
