"""Normalizing `rbac_tags` across the three shapes it has historically had.

Revision 0004 settled the column on a JSON array of strings. That was chosen over
`{tag: true}` because it round-trips 1:1 with the three other places tags already appear
as lists — `RawFileStore.save(rbac_tags: List[str])`, the comma-separated
`/ingest/upload` form field, and the JWT claim. Only the database writer was a dict.

This function still accepts every old shape, because fixtures on disk predate the
migration and `app/pipeline.py` loads them verbatim. The `"key:value"` collapse matches
the SQL in 0004 exactly; change one and you must change the other.
"""

from __future__ import annotations

from typing import Iterable


def normalize_tags(raw: object) -> list[str]:
    """Coerce any historical `rbac_tags` shape to a sorted, deduped list of strings.

    - ``None``                          -> ``[]``
    - ``"a, b"``                        -> ``["a", "b"]``
    - ``["a", "b"]``                    -> ``["a", "b"]``
    - ``{"a": True}``                   -> ``["a"]``          (ingestion's shape)
    - ``{"confidentiality": "internal"}`` -> ``["confidentiality:internal"]``  (fixtures)
    """
    if raw is None:
        return []

    if isinstance(raw, str):
        candidates: Iterable[str] = raw.split(",")
    elif isinstance(raw, dict):
        candidates = [
            key if isinstance(value, bool) else f"{key}:{value}" for key, value in raw.items()
        ]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        candidates = [str(item) for item in raw]
    else:
        candidates = [str(raw)]

    seen: set[str] = set()
    for candidate in candidates:
        stripped = candidate.strip()
        if stripped:
            seen.add(stripped)
    return sorted(seen)
