"""Hand an unresolved ambiguity to the Human Review Queue.

Reached only after the retry budget in `app.ambiguity` is exhausted. The candidate edge
is persisted as `pending_review` — visible, but treated as absent by the Risk Engine —
and paired with an escalation record carrying the full retry trace.
"""

from __future__ import annotations

from app.ambiguity.resolver import Resolution
from app.schemas import (
    CandidateEdge,
    EscalationReason,
    EscalationRecord,
    EscalationSource,
    KGEdge,
    ReviewStatus,
)


def escalate(
    candidate: CandidateEdge, resolution: Resolution
) -> tuple[KGEdge, str, EscalationRecord]:
    """Return the pending edge, its escalation id, and the escalation record."""
    if resolution.resolved:
        raise ValueError(f"{candidate.edge_id} was resolved; nothing to escalate")

    edge = KGEdge(
        edge_id=candidate.edge_id,
        document_id=candidate.document_id,
        src_clause_ref=candidate.src_clause_ref,
        dst_clause_ref=candidate.dst_clause_ref,
        # The queue item has to name a single type to render; we surface the reading the
        # extractor thought most likely and leave the alternatives in the trace.
        edge_type=candidate.candidate_types[0],
        status=ReviewStatus.PENDING_REVIEW,
        confidence=candidate.confidence,
        evidence_chunk_ids=candidate.evidence_chunk_ids,
        pattern_key=candidate.pattern_key,
        resolved_by=None,
    )

    record = EscalationRecord(
        status=ReviewStatus.PENDING_REVIEW,
        source=EscalationSource.CLAUSE_NER,
        reason=EscalationReason.AMBIGUOUS_EDGE,
        document_id=candidate.document_id,
        clause_ref=candidate.src_clause_ref,
        rounds_attempted=resolution.rounds_attempted,
        trace=resolution.trace,
    )
    return edge, f"ESC-{candidate.edge_id}", record


def confirm(candidate: CandidateEdge, resolution: Resolution) -> KGEdge:
    """Commit an edge the retry loop settled."""
    if not resolution.resolved:
        raise ValueError(f"{candidate.edge_id} is unresolved; escalate it instead")

    return KGEdge(
        edge_id=candidate.edge_id,
        document_id=candidate.document_id,
        src_clause_ref=candidate.src_clause_ref,
        dst_clause_ref=candidate.dst_clause_ref,
        edge_type=resolution.edge_type or candidate.candidate_types[0],
        status=ReviewStatus.CONFIRMED,
        confidence=resolution.confidence,
        evidence_chunk_ids=candidate.evidence_chunk_ids,
        pattern_key=candidate.pattern_key,
        resolved_by=resolution.resolved_by,
    )
