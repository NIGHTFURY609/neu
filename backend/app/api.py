"""Read endpoints for downstream stages.

Two routes only. The KG route exists mainly so the "confirmed edges only" rule from
ARCHITECTURE.md §3.3 is enforced in one place instead of in every reader.

The Review Queue endpoints are Dev 1's. This stage writes `escalations` rows; it does
not serve or resolve them.
"""

from fastapi import Depends, FastAPI, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.kg import store
from app.schemas import Fact, KGEdge, ReviewStatus

app = FastAPI(title="Clause NER + Knowledge Graph", version="0.1.0")


@app.get("/documents/{document_id}/facts", response_model=list[Fact])
def get_facts(document_id: str, session: Session = Depends(get_session)) -> list[Fact]:
    return store.list_facts(session, document_id)


@app.get("/documents/{document_id}/kg/edges", response_model=list[KGEdge])
def get_edges(
    document_id: str,
    status: ReviewStatus = Query(
        default=ReviewStatus.CONFIRMED,
        description="Defaults to confirmed. The Risk Engine must not read anything else.",
    ),
    session: Session = Depends(get_session),
) -> list[KGEdge]:
    return store.list_edges(session, document_id, status=status)
