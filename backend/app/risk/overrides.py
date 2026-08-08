"""Knowledge Graph override check — Dev 4's `mike.py`, ported from `better-call-saul/`.

Takes a clause flagged by `evaluator.py` and checks whether a *confirmed* KG edge
legally excuses it — e.g. a CEO-approved override recorded elsewhere in the document.
Read-only against the KG; never writes, never mutates an edge.

`KGEdgeLike` is a structural stand-in rather than importing `app.schemas.KGEdge`
directly — it matches that model's field names exactly (verified at integration:
`app.schemas.KGEdge` has edge_id/document_id/src_clause_ref/dst_clause_ref/edge_type/
status), so real `KGEdge` instances satisfy this Protocol without adaptation. Kept as a
Protocol rather than a hard import so this module stays independently testable against
plain mock objects, same as it was pre-integration.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from app.risk.rules import Rule

CONFIRMED = "confirmed"  # matches app.schemas.ReviewStatus.CONFIRMED's value

_EDGE_STR_FIELDS = ("edge_id", "document_id", "src_clause_ref", "dst_clause_ref", "edge_type", "status")


@runtime_checkable
class KGEdgeLike(Protocol):
    edge_id: str
    document_id: str
    src_clause_ref: str
    dst_clause_ref: str
    edge_type: str
    status: str


def _validate_edge(edge: object, document_id: str) -> KGEdgeLike:
    if not isinstance(edge, KGEdgeLike):
        raise TypeError(f"Expected a KG edge with {', '.join(_EDGE_STR_FIELDS)}, got {type(edge).__name__}")
    # runtime_checkable only confirms these attributes exist, not that they're strings —
    # check that ourselves so a wrong-typed field raises here instead of failing to match
    # silently three lines down.
    for attr in _EDGE_STR_FIELDS:
        value = getattr(edge, attr)
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f"KG edge {attr!r} must be a non-empty string, got {value!r}")
    if edge.document_id != document_id:
        raise ValueError(
            f"edge {edge.edge_id!r} belongs to document {edge.document_id!r}, not {document_id!r} — "
            f"caller must scope kg_edges to the document being evaluated"
        )
    return edge


def find_confirmed_overrides(
    document_id: str, clause_ref: str, rule: Rule, kg_edges: Sequence[KGEdgeLike]
) -> tuple[KGEdgeLike, ...]:
    """Confirmed KG edges that legally excuse `rule` firing on `clause_ref` in `document_id`.

    Direction matters: an edge overrides/waives/modifies the clause it points AT, not the
    clause it originates from — "Section 4.1 --OVERRIDES--> Section 2.2" excuses 2.2, not
    4.1. Matches on dst_clause_ref == clause_ref.

    document_id is enforced, not just assumed — every edge is checked against it, so a
    caller bug that leaks an edge from the wrong document raises here instead of silently
    corrupting which clause's compliance gets assessed.

    kg_edges is still expected to already be confirmed-only (e.g. via
    `app.kg.store.list_edges(document_id, status=CONFIRMED)`) — status is re-checked below
    anyway. That's deliberate defense-in-depth, not redundancy: never let a single upstream
    filter be the only thing standing between an unconfirmed edge and a compliance decision.
    """
    if not isinstance(document_id, str) or not document_id.strip():
        raise TypeError(f"document_id must be a non-empty string, got {document_id!r}")
    if not isinstance(clause_ref, str):
        raise TypeError(f"clause_ref must be a string, got {type(clause_ref).__name__}")
    clause_ref = clause_ref.strip()
    if not clause_ref:
        raise ValueError("clause_ref cannot be empty.")

    if not rule.allowed_overrides:
        return ()

    matches: dict[str, KGEdgeLike] = {}  # keyed by edge_id — dedupe by identity, not object hash
    for raw_edge in kg_edges:
        if not isinstance(raw_edge, KGEdgeLike):
            raise TypeError(f"Expected a KG edge with {', '.join(_EDGE_STR_FIELDS)}, got {type(raw_edge).__name__}")
        # document_id is checked unconditionally, before relevance — a wrong-document edge
        # is the caller's scoping bug, not a content-quality issue, and must never slip
        # through silently just because it happens to be irrelevant to this clause_ref.
        if raw_edge.document_id != document_id:
            raise ValueError(
                f"edge {getattr(raw_edge, 'edge_id', '?')!r} belongs to document "
                f"{raw_edge.document_id!r}, not {document_id!r} — caller must scope "
                f"kg_edges to the document being evaluated"
            )
        # Relevance check next, via a lenient read (!= never raises) — an edge irrelevant
        # to this clause shouldn't be able to poison this lookup just because ITS OWN
        # OTHER fields happen to be malformed. Only a matching edge gets full validation.
        if raw_edge.dst_clause_ref != clause_ref:
            continue
        edge = _validate_edge(raw_edge, document_id)
        if edge.status != CONFIRMED:
            continue
        if edge.edge_type not in rule.allowed_overrides:
            continue

        existing = matches.get(edge.edge_id)
        if existing is not None and (
            existing.src_clause_ref != edge.src_clause_ref
            or existing.dst_clause_ref != edge.dst_clause_ref
            or existing.edge_type != edge.edge_type
            or existing.status != edge.status
        ):
            raise ValueError(f"conflicting data for duplicate edge_id {edge.edge_id!r}")
        matches[edge.edge_id] = edge

    return tuple(sorted(matches.values(), key=lambda e: (e.src_clause_ref, e.edge_type, e.edge_id)))
