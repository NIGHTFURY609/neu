"""Serving redlines to the dashboard.

Replaces `dashboard_stubs.get_redlines`. The default of `status=confirmed` is the whole
point of the route: a `pending_review` redline is one the loop could not stand behind,
and §4.1 says it waits for a human. Reading it back is an explicit act.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

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
) -> list[Redline]:
    return store.list_redlines(session, document_id, status=status)


@router.get("/redlines/{redline_id}", response_model=Redline)
def get_redline(redline_id: str, session: Session = Depends(get_session)) -> Redline:
    """One redline by id, whatever its status.

    No status filter here on purpose — this is the route the Review Queue links to, and
    a reviewer deciding on a held redline has to be able to see it.
    """
    redline = store.get_redline(session, redline_id)
    if redline is None:
        raise HTTPException(status_code=404, detail=f"No redline {redline_id}")
    return redline
