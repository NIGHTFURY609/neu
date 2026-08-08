"""Who is making the request, and what they are allowed to see."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

WILDCARD = "*"


@dataclass(frozen=True)
class Principal:
    """An authenticated (or dev-mode) caller.

    `rbac_tags` is the whole authorization model: a document is visible when the
    principal holds at least one of its tags. There are no roles-to-permissions tables —
    `role` is carried for display and for the one write-vs-read distinction in
    `deps.require_principal`, not for access decisions.
    """

    user_id: str
    email: str | None = None
    role: str = "anonymous"
    rbac_tags: frozenset[str] = frozenset()
    claims: dict = field(default_factory=dict, repr=False)

    @property
    def is_superuser(self) -> bool:
        return WILDCARD in self.rbac_tags

    def can_access(self, doc_tags: Iterable[str] | None) -> bool:
        """Deny by default.

        An untagged document is unreachable rather than public. That is safe to assume
        because `RawFileStore._validate` refuses to store an untagged upload in the first
        place, and revision 0004 backfilled the legacy rows that predated it — so a
        document with no tags is a bug, and failing closed is the right response to one.
        """
        if self.is_superuser:
            return True
        return bool(self.rbac_tags & set(doc_tags or ()))
