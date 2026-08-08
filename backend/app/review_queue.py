"""Human Review Queue — Dev 1.

Two escalation sources feed one queue (WORK-SPLIT.md): ambiguous KG edges from Clause
NER, and exhausted-budget / low-confidence redlines from the Redline Generator. Both
write the same `EscalationRecord`, so this serves and resolves both through one shape.

Resolution is not just a status flip on the queue row — it writes back to the candidate
KG edge too, which is what makes a reviewer's decision visible to the Risk Engine. That
part lives in `store.resolve_escalation`.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.kg import store
from app.schemas import EdgeType, EscalationItem, EscalationSource, ReviewStatus

router = APIRouter(prefix="/review-queue", tags=["review-queue"])


class ResolveRequest(BaseModel):
    """A human decision.

    `status` deliberately excludes `pending_review` — resolving to "still pending" is
    not a decision, and accepting it would leave the item in the queue with a
    `reviewer_id` attached, which reads as resolved to anyone scanning the table.
    """

    status: Literal[ReviewStatus.CONFIRMED, ReviewStatus.REJECTED]
    reviewer_id: str = Field(min_length=1)
    # Only meaningful for `clause_ner` escalations: the edge was escalated because the
    # extractor could not choose between candidate types, so the reviewer picks one.
    edge_type: EdgeType | None = None


@router.get("", response_model=list[EscalationItem])
def list_queue(
    status: list[ReviewStatus] = Query(
        default=[ReviewStatus.PENDING_REVIEW],
        description="Repeatable. Defaults to the open queue; pass all three to see history.",
    ),
    document_id: str | None = Query(default=None),
    source: EscalationSource | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[EscalationItem]:
    return store.list_escalations(
        session, statuses=status, document_id=document_id, source=source
    )


@router.get("/{escalation_id}", response_model=EscalationItem)
def get_queue_item(
    escalation_id: str, session: Session = Depends(get_session)
) -> EscalationItem:
    item = store.get_escalation(session, escalation_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No escalation {escalation_id}")
    return item


@router.post("/{escalation_id}/resolve", response_model=EscalationItem)
def resolve(
    escalation_id: str,
    body: ResolveRequest,
    session: Session = Depends(get_session),
) -> EscalationItem:
    current = store.get_escalation(session, escalation_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"No escalation {escalation_id}")
    if current.status is not ReviewStatus.PENDING_REVIEW:
        # Not an error the reviewer caused — someone else got there first. 409 so the
        # UI can say that rather than silently overwriting the earlier decision.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Escalation {escalation_id} was already {current.status.value}"
                f" by {current.reviewer_id}"
            ),
        )

    item = store.resolve_escalation(
        session,
        escalation_id,
        status=body.status,
        reviewer_id=body.reviewer_id,
        edge_type=body.edge_type,
    )
    session.commit()
    return item
