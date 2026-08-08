"""Read endpoints for downstream stages, plus the app the frontend talks to.

The two routes below are Clause NER's own. The KG route exists mainly so the "confirmed
edges only" rule from ARCHITECTURE.md §3.3 is enforced in one place instead of in every
reader.

Mounted here: the Review Queue (`app.review_queue`, Dev 1), the Redline Generator's
routes (`app.redline.routes`), a temporary dashboard stub standing in for Dev 4's Risk
Engine (`app.dashboard_stubs`), and Dev 2's ingestion routes under `/ingest`.

Ingestion is mounted rather than served by its own uvicorn because both apps defaulted to
port 8000 and collided; one process now owns the port the frontend is configured against.
"""

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import dashboard_stubs, review_queue
from app.db import get_session
from app.kg import store
from app.redline import routes as redline_routes
from app.schemas import Fact, KGEdge, ReviewStatus
from ingestion import api as ingestion_routes

app = FastAPI(title="Legal Intelligence Copilot API", version="0.1.0")

# The Vite dev server runs on a different origin than uvicorn.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review_queue.router)
app.include_router(redline_routes.router)
app.include_router(dashboard_stubs.router)
app.include_router(ingestion_routes.router, prefix="/ingest")


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
