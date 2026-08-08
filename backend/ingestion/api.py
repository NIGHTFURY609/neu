"""Dev 2's routes, mounted on the one app the frontend talks to.

An `APIRouter`, not a `FastAPI` app. Running a second uvicorn for ingestion put it on
port 8000, which is the port Dev 3's app already owns — whichever started second lost.
`app.api` includes this under `/ingest`.

`/upload` runs the whole pipeline. It previously called the OCR engine directly and
returned the extracted text, which meant an uploaded document was never chunked, never
embedded and never written anywhere Dev 3 could read it — the upload appeared to work
and nothing downstream ever saw the document.

It also runs Dev 3's stages against those same chunks — see `app.orchestration` for why
nothing downstream ever ran automatically before. Those stages used to run *inline*, which
kept this request open for the whole chain; under `LLM_MODE=live` that is minutes of a
spinner with no way to report progress. They are now queued as a background task and the
caller polls `GET /documents/{id}/status`.

`app` imports are function-local, same as `ingestion.postgres_sync`: this package stays
importable, and this route stays working, without a reachable database.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from app.config import settings
from ingestion.ingestion_pipeline import IngestionPipeline
from ingestion.ocr_engine import UniversalOCREngine

logger = logging.getLogger("ingestion.api")
router = APIRouter(tags=["ingestion"])

# MarkItDown handles PDF/DOCX/PPTX/XLSX/HTML. Its import is lazy (inside `run`), so the
# package stays importable without it — see the `ocr` extra in pyproject.toml.
pipeline = IngestionPipeline(ocr_engine=UniversalOCREngine(), sync_to_postgres=True)


def _principal_dep():
    """Resolve `require_principal` lazily.

    Imported inside the function for the same reason every other `app` import here is:
    `ingestion` must stay importable on its own. Declared as a module-level `Depends`
    target so FastAPI still sees a real dependency in the signature.
    """
    from app.auth.deps import require_principal

    return require_principal


@router.post("/upload")
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    rbac_tags: str = Form(""),
    doc_kind: str = Form("contract"),
    jurisdiction: str | None = Form(None),
    title: str | None = Form(None),
    parent_document_id: str | None = Form(None),
    process: bool = Form(True),
    principal=Depends(_principal_dep()),
) -> dict:
    """Upload a document, then queue the stages behind it.

    `uploader_id` is gone: it was a caller-supplied form field, so it recorded a claim
    rather than a fact. It now comes from the authenticated principal.

    `rbac_tags` narrows rather than grants. A caller may tag an upload with any subset of
    the tags they themselves hold; asking for a tag you do not hold is a 403, because it
    would let you file a document into a compartment you cannot read back — which looks
    like data loss, not like an access error. Empty means "everything I hold".
    """
    requested = [t.strip() for t in rbac_tags.split(",") if t.strip()]
    if principal.is_superuser:
        from app.config import settings

        effective = requested or [
            t.strip() for t in settings.default_rbac_tags.split(",") if t.strip()
        ]
    else:
        held = set(principal.rbac_tags)
        effective = sorted(set(requested) & held) if requested else sorted(held)
        if not effective:
            raise HTTPException(
                403,
                "you cannot file a document under tags you do not hold "
                f"(requested: {requested or 'none'}; you hold: {sorted(held) or 'none'})",
            )

    doc = pipeline.ingest(
        file_bytes=await file.read(),
        original_filename=file.filename or "upload",
        uploader_id=principal.user_id,
        rbac_tags=effective,
    )
    chunks = pipeline.vector_store.get_chunks_for_document(doc.document_id)

    # Classification, lineage and job state live on the Postgres row, which
    # `postgres_sync` has already written by this point. Best-effort, same as before: an
    # upload is real — raw file, Metadata DB and Vector DB rows all exist — whether or not
    # Postgres is reachable, and failing here must not undo it.
    queued = False
    try:
        queued = _record_lineage_and_queue(
            background,
            document_id=doc.document_id,
            doc_kind=doc_kind,
            jurisdiction=jurisdiction,
            title=title,
            parent_document_id=parent_document_id,
            principal=principal,
            process=process,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("could not queue processing for document %s", doc.document_id)

    return {
        "document_id": doc.document_id,
        "filename": doc.filename,
        "status": doc.status.value,
        "pages": doc.page_count,
        "chunks": len(chunks),
        # §7: the worst page in the document, not an average. A single bad scan is what
        # caps every redline written off this document downstream.
        "min_ocr_confidence": min((c.ocr_confidence for c in chunks), default=0.0),
        "low_confidence_pages": pipeline.metadata_db.get_low_confidence_pages(doc.document_id),
        "processing": {
            "status": "queued" if queued else "pending",
            "poll": f"/documents/{doc.document_id}/status",
        },
    }


def _record_lineage_and_queue(
    background: BackgroundTasks,
    *,
    document_id: str,
    doc_kind: str,
    jurisdiction: str | None,
    title: str | None,
    parent_document_id: str | None,
    principal,
    process: bool,
) -> bool:
    from app import models
    from app.auth.rbac import authorize_document
    from app.config import settings
    from app.db import SessionLocal
    from app.processing import run_processing_job
    from sqlalchemy import func, select

    with SessionLocal() as session:
        document = session.get(models.Document, document_id)
        if document is None:
            logger.warning("no Postgres row for %s; skipping lineage", document_id)
            return False

        document.doc_kind = doc_kind
        document.title = title or document.filename
        document.jurisdiction = jurisdiction or settings.default_jurisdiction

        if parent_document_id:
            # You cannot declare a revision of a document you cannot read.
            authorize_document(session, parent_document_id, principal)
            parent = session.get(models.Document, parent_document_id)
            if parent is None:
                raise HTTPException(404, f"parent document {parent_document_id} not found")
            family = parent.contract_family or parent.document_id
            # max(version)+1 across the family, NOT parent.version+1: two uploads naming
            # the same parent would both claim the same version and collide on the
            # (contract_family, version) unique constraint as a 500.
            highest = session.scalar(
                select(func.max(models.Document.version)).where(
                    models.Document.contract_family == family
                )
            )
            document.contract_family = family
            document.version = (highest or parent.version) + 1
            document.parent_document_id = parent_document_id
        else:
            document.contract_family = document_id
            document.version = 1

        if process:
            document.processing_status = "queued"
        session.commit()

    if process:
        # Only the id crosses the boundary — `run_processing_job` opens its own session.
        background.add_task(run_processing_job, document_id)
    return process
