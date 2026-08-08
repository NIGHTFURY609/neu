"""GET /documents/{id}/summary."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models
from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document
from app.db import get_session
from app.summary.schemas import SummaryResponse
from app.summary.service import get_or_generate

router = APIRouter(tags=["summary"])


@router.get("/documents/{document_id}/summary", response_model=SummaryResponse)
def get_summary(
    document_id: str,
    refresh: bool = Query(default=False, description="Regenerate even if a summary is cached."),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> SummaryResponse:
    """A grounded summary of the document.

    `cached` and `stale` are both returned rather than the endpoint silently regenerating
    on staleness: in live mode regeneration is a paid call, and a reviewer is better
    served by "this was written before the last decision, refresh?" than by a surprise
    latency spike and quietly different text.
    """
    document = session.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, f"document {document_id} not found")
    authorize_document(session, document_id, principal)
    return get_or_generate(session, document, refresh=refresh)
