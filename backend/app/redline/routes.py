"""Serving redlines to the dashboard.

Replaces `dashboard_stubs.get_redlines`. The default of `status=confirmed` is the whole
point of the route: a `pending_review` redline is one the loop could not stand behind,
and §4.1 says it waits for a human. Reading it back is an explicit act.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import demo_data
from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document
from app.config import settings
from app.db import get_session
from app.redline import store
from app.schemas import Redline, ReviewStatus

router = APIRouter(tags=["redlines"])


@router.get("/documents/{document_id}/redlines", response_model=list[Redline])
def get_redlines(
    document_id: str,
    status: ReviewStatus = Query(
        default=ReviewStatus.CONFIRMED,
        description=(
            "Defaults to confirmed. A pending_review redline has not been approved by a "
            "human and must not be presented as a suggestion."
        ),
    ),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[Redline]:
    authorize_document(session, document_id, principal)
    if settings.demo_mode:
        return demo_data.get_demo_data().list_redlines(document_id, status)
    return store.list_redlines(session, document_id, status=status)


@router.get("/redlines/{redline_id}", response_model=Redline)
def get_redline(
    redline_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> Redline:
    """One redline by id, whatever its status.

    No status filter here on purpose — this is the route the Review Queue links to, and
    a reviewer deciding on a held redline has to be able to see it.

    This is the one route with no `document_id` in its path, which makes it the easiest
    place to forget the RBAC check — and the most damaging, since it serves full clause
    text. The document is resolved from the redline before it is returned.
    """
    redline = (
        demo_data.get_demo_data().get_redline(redline_id)
        if settings.demo_mode
        else store.get_redline(session, redline_id)
    )
    if redline is None:
        raise HTTPException(status_code=404, detail=f"No redline {redline_id}")
    authorize_document(session, redline.document_id, principal)
    return redline
