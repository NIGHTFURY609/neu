"""Turning a `Principal` into a SQL predicate and an HTTP gate."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app import models
from app.auth.principal import Principal


def visible_documents(principal: Principal) -> sa.ColumnElement[bool]:
    """Predicate over `documents` for the tag-overlap rule.

    Filtering happens in SQL rather than in Python because several callers only ever see
    a count — the dashboard's "awaiting review" stat, for instance — and a Python filter
    applied after the query would leak the true totals through those numbers.

    `has_any` emits the JSONB `?|` operator with the escaping psycopg needs; hand-writing
    `op("?|")` gets the escaping wrong. It is matched by the GIN index revision 0004
    creates with the default `jsonb_ops` opclass — `jsonb_path_ops` does not support `?|`
    and would leave this correct but unindexed.
    """
    if principal.is_superuser:
        return sa.true()
    tags = sorted(principal.rbac_tags)
    if not tags:
        return sa.false()
    return models.Document.rbac_tags.has_any(
        sa.cast(postgresql.array(tags), postgresql.ARRAY(sa.Text))
    )


def authorize_document(
    session: Session, document_id: str, principal: Principal
) -> models.Document | None:
    """Fetch a document the principal may see, or raise.

    404 when it does not exist, 403 when it exists but the tags do not overlap. 403 leaks
    existence, which is the wrong default for a public API — but for this tool a reviewer
    needs to be told "you do not hold `legal-team`" rather than being shown a blank page,
    and the alternative is indistinguishable from a bug. Comparison endpoints, where the
    ids are supplied by the caller rather than followed from a list, use 404 instead.

    Returns None for a superuser without touching the database. That fast path exists
    because the route tests override `get_session` with a stub that has no `.get`, and
    because a wildcard principal has nothing to check.
    """
    if principal.is_superuser:
        return None

    document = session.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, f"document {document_id} not found")
    if not principal.can_access(document.rbac_tags):
        held = ", ".join(sorted(principal.rbac_tags)) or "no tags"
        raise HTTPException(
            403, f"you do not hold an RBAC tag for document {document_id} (you hold: {held})"
        )
    return document
