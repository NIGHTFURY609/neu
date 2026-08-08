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
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_principal, require_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document
from app.db import get_session
from app.kg import store
from app.schemas import EdgeType, EscalationItem, EscalationSource, ReviewStatus

router = APIRouter(prefix="/review-queue", tags=["review-queue"])


class ResolveRequest(BaseModel):
    """A human decision.

    `status` deliberately excludes `pending_review` — resolving to "still pending" is
    not a decision, and accepting it would leave the item in the queue with a
    `reviewer_id` attached, which reads as resolved to anyone scanning the table.

    `reviewer_id` is deliberately absent. It used to be a free-text field here, which
    meant anyone could attribute any decision to anyone — directly undermining the §4.2
    guarantee that a human-confirmed fact stays distinguishable from an AI-generated one,
    since the attribution itself was unverified. It now comes from the authenticated
    principal. The field is removed rather than ignored: leaving it declared and required
    would 422 every request, and leaving it optional-but-ignored invites a caller to
    believe it still does something.
    """

    status: Literal[ReviewStatus.CONFIRMED, ReviewStatus.REJECTED]
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
    principal: Principal = Depends(get_principal),
) -> list[EscalationItem]:
    # Filtered in SQL inside the store, not here: the dashboard renders this as a bare
    # count, and a Python filter applied afterwards would still leak the true total.
    return store.list_escalations(
        session, statuses=status, document_id=document_id, source=source, principal=principal
    )


@router.get("/{escalation_id}", response_model=EscalationItem)
def get_queue_item(
    escalation_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> EscalationItem:
    item = store.get_escalation(session, escalation_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No escalation {escalation_id}")
    authorize_document(session, item.document_id, principal)
    return item


@router.post("/{escalation_id}/resolve", response_model=EscalationItem)
def resolve(
    escalation_id: str,
    body: ResolveRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_principal),
) -> EscalationItem:
    current = store.get_escalation(session, escalation_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"No escalation {escalation_id}")
    authorize_document(session, current.document_id, principal)
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
        reviewer_id=principal.user_id,
        edge_type=body.edge_type,
    )
    session.commit()
    return item
