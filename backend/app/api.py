"""Read endpoints for downstream stages, plus the app the frontend talks to.

The two routes below are Clause NER's own. The KG route exists mainly so the "confirmed
edges only" rule from ARCHITECTURE.md §3.3 is enforced in one place instead of in every
reader.

Mounted here: the Review Queue (`app.review_queue`, Dev 1), the Redline Generator's
routes (`app.redline.routes`), the Risk Engine's routes (`app.risk.routes`), and Dev 2's
ingestion routes under `/ingest`.

Ingestion is mounted rather than served by its own uvicorn because both apps defaulted to
port 8000 and collided; one process now owns the port the frontend is configured against.
"""

import logging

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import documents as document_routes
from app import review_queue
from app.compare import routes as compare_routes
from app.negotiation import routes as negotiation_routes
from app.regulations import routes as regulation_routes
from app.search import routes as search_routes
from app.summary import routes as summary_routes
from app.auth.deps import get_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document
from app.config import settings
from app.db import get_session
from app.kg import store
from app.redline import routes as redline_routes
from app.risk import routes as risk_routes
from app.schemas import Fact, KGEdge, ReviewStatus
from ingestion import api as ingestion_routes

logger = logging.getLogger("app.api")

app = FastAPI(title="Legal Intelligence Copilot API", version="0.1.0")

if settings.auth_mode != "supabase":
    logger.warning(
        "AUTH_MODE=%s — caller identity is NOT verified. rbac_tags are still enforced, "
        "but anyone can claim any identity. Set AUTH_MODE=supabase for a real deployment.",
        settings.auth_mode,
    )

# The Vite dev server runs on a different origin than uvicorn.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review_queue.router)
app.include_router(redline_routes.router)
app.include_router(risk_routes.router)
app.include_router(document_routes.router)
app.include_router(compare_routes.router)
app.include_router(search_routes.router)
app.include_router(regulation_routes.router)
app.include_router(summary_routes.router)
app.include_router(negotiation_routes.router)
app.include_router(ingestion_routes.router, prefix="/ingest")


@app.get("/documents/{document_id}/facts", response_model=list[Fact])
def get_facts(
    document_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[Fact]:
    authorize_document(session, document_id, principal)
    return store.list_facts(session, document_id)


@app.get("/documents/{document_id}/kg/edges", response_model=list[KGEdge])
def get_edges(
    document_id: str,
    status: ReviewStatus = Query(
        default=ReviewStatus.CONFIRMED,
        description="Defaults to confirmed. The Risk Engine must not read anything else.",
    ),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[KGEdge]:
    authorize_document(session, document_id, principal)
    return store.list_edges(session, document_id, status=status)
