"""Authentication and RBAC enforcement.

ARCHITECTURE.md §7 recorded RBAC as "a label not enforcement": `documents.rbac_tags` was
written by ingestion and read by no query, and `RawFileStore.read` — the only
access-control code in the repo — had no callers. This package closes that.

Layout mirrors the split between the three concerns, so each stays independently
readable:

    principal.py  who the caller is, and whether they may see a set of tags
    jwt.py        verifying a Supabase access token
    tags.py       normalizing the three historical shapes of `rbac_tags`
    deps.py       the FastAPI dependencies routes actually depend on
    rbac.py       the SQL predicate and the 404/403 document gate
"""
