"""Phase 1 RBAC: a document is visible only to the principal that owns it.

No superuser fast path here — a wildcard-tag principal gets no special treatment, and a
document with no owner (everything that predates revision 0009) is unreachable by
anyone. `visible_documents` is checked by compiling its predicate to SQL rather than
running it, since it needs a real Postgres engine to evaluate meaningfully and this
suite otherwise never touches one.
"""

import pytest
from fastapi import HTTPException

from app import models
from app.auth.principal import Principal
from app.auth.rbac import authorize_document, visible_documents


class FakeSession:
    """Enough of a Session for `authorize_document`: get by primary key."""

    def __init__(self, document: models.Document | None) -> None:
        self._document = document

    def get(self, model, pk):
        assert model is models.Document
        if self._document is not None and pk == self._document.document_id:
            return self._document
        return None


def _document(owner_id: str | None) -> models.Document:
    return models.Document(document_id="DOC-1", filename="f.pdf", owner_id=owner_id)


def test_owner_can_access_their_document():
    doc = _document(owner_id="u1")
    result = authorize_document(FakeSession(doc), "DOC-1", Principal(user_id="u1"))
    assert result is doc


def test_a_different_owner_is_forbidden():
    doc = _document(owner_id="u1")
    with pytest.raises(HTTPException) as exc_info:
        authorize_document(FakeSession(doc), "DOC-1", Principal(user_id="u2"))
    assert exc_info.value.status_code == 403


def test_wildcard_tags_grant_no_special_access():
    """Decision: no superuser bypass in phase 1, even for a `*`-tag holder."""
    doc = _document(owner_id="u1")
    wildcard = Principal(user_id="u2", rbac_tags=frozenset({"*"}))
    with pytest.raises(HTTPException) as exc_info:
        authorize_document(FakeSession(doc), "DOC-1", wildcard)
    assert exc_info.value.status_code == 403


def test_an_orphaned_document_is_unreachable_by_anyone():
    """Pre-RBAC rows (owner_id NULL) stay unreachable; there is no admin role yet."""
    orphan = _document(owner_id=None)
    wildcard = Principal(user_id="u1", rbac_tags=frozenset({"*"}))
    with pytest.raises(HTTPException) as exc_info:
        authorize_document(FakeSession(orphan), "DOC-1", wildcard)
    assert exc_info.value.status_code == 403


def test_missing_document_is_404():
    with pytest.raises(HTTPException) as exc_info:
        authorize_document(FakeSession(None), "DOC-1", Principal(user_id="u1"))
    assert exc_info.value.status_code == 404


def test_visible_documents_predicate_filters_on_owner_id():
    predicate = visible_documents(Principal(user_id="u1"))
    compiled = str(predicate.compile(compile_kwargs={"literal_binds": True}))
    assert "documents.owner_id" in compiled
    assert "u1" in compiled


def test_visible_documents_denies_a_principal_with_no_user_id():
    import sqlalchemy as sa

    predicate = visible_documents(Principal(user_id=""))
    assert predicate.compare(sa.false())
