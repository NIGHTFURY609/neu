"""Coverage for `app.auth.principal.Principal.can_access` — the tag-intersection logic.

`app.auth.rbac` no longer calls this (phase 1 RBAC moved to per-owner access, see
`test_rbac.py`), but the method is still live code, kept for a later sharing phase, so
its own contract is still worth pinning directly.
"""

from app.auth.principal import Principal


def test_superuser_sees_everything_regardless_of_tags():
    superuser = Principal(user_id="u1", rbac_tags=frozenset({"*"}))

    assert superuser.can_access(["some-tag"]) is True
    assert superuser.can_access([]) is True
    assert superuser.can_access(None) is True


def test_overlapping_tags_grant_access():
    principal = Principal(user_id="u1", rbac_tags=frozenset({"legal-team"}))

    assert principal.can_access(["legal-team", "finance"]) is True


def test_disjoint_tags_deny_access():
    principal = Principal(user_id="u1", rbac_tags=frozenset({"legal-team"}))

    assert principal.can_access(["finance"]) is False


def test_untagged_document_is_unreachable_by_default():
    """Deny by default: a document with no tags is a bug, not a public document."""
    principal = Principal(user_id="u1", rbac_tags=frozenset({"legal-team"}))

    assert principal.can_access(None) is False
    assert principal.can_access([]) is False
