"""Turning a `Principal` into a SQL predicate and an HTTP gate."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models
from app.auth.principal import Principal


def visible_documents(principal: Principal) -> sa.ColumnElement[bool]:
    """Predicate over `documents` for the owner-only rule (phase 1 RBAC).

    Filtering happens in SQL rather than in Python because several callers only ever see
    a count — the dashboard's "awaiting review" stat, for instance — and a Python filter
    applied after the query would leak the true totals through those numbers.

    A document is visible only to the principal whose user_id uploaded it. Rows with no
    owner (everything that predates revision 0009) are unreachable by anyone. The tag
    model this replaced (`rbac_tags` overlap, `Principal.is_superuser`) is left in place
    as data and dormant code for a later sharing phase, not read here.
    """
    if not principal.user_id:
        return sa.false()
    return models.Document.owner_id == principal.user_id


def authorize_document(session: Session, document_id: str, principal: Principal) -> models.Document:
    """Fetch a document the principal owns, or raise.

    404 when it does not exist, 403 when it exists but belongs to someone else. 403 leaks
    existence, which is the wrong default for a public API — but for this tool a reviewer
    needs to be told "this isn't yours" rather than being shown a blank page, and the
    alternative is indistinguishable from a bug. Comparison endpoints, where the ids are
    supplied by the caller rather than followed from a list, use 404 instead.

    No superuser fast path in phase 1: every principal, including a wildcard-tag holder,
    is subject to the same ownership check.
    """
    document = session.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, f"document {document_id} not found")
    if document.owner_id != principal.user_id:
        raise HTTPException(403, f"you are not the owner of document {document_id}")
    return document
