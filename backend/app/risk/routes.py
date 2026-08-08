"""Serving risk flags to the dashboard.

Replaces `dashboard_stubs.get_risk_flags`. Unlike the KG edges/redlines routes, there is
no confirmed-only default here — the dashboard renders suppressed flags too (with a
"waived" treatment), so `status` is opt-in filtering, not a safety default.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document
from app.db import get_session
from app.risk import store
from app.schemas import RiskFlag, RiskFlagStatus

router = APIRouter(tags=["risk"])


@router.get("/documents/{document_id}/risk-flags", response_model=list[RiskFlag])
def get_risk_flags(
    document_id: str,
    status: RiskFlagStatus | None = Query(default=None, description="Omit to see every status."),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RiskFlag]:
    authorize_document(session, document_id, principal)
    return store.list_risk_flags(session, document_id, status=status)
