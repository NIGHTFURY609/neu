"""Dev 2's routes, mounted on the one app the frontend talks to.

An `APIRouter`, not a `FastAPI` app. Running a second uvicorn for ingestion put it on
port 8000, which is the port Dev 3's app already owns — whichever started second lost.
`app.api` includes this under `/ingest`.

`/upload` runs the whole pipeline. It previously called the OCR engine directly and
returned the extracted text, which meant an uploaded document was never chunked, never
embedded and never written anywhere Dev 3 could read it — the upload appeared to work
and nothing downstream ever saw the document.
"""
from fastapi import APIRouter, File, Form, UploadFile

from ingestion.ingestion_pipeline import IngestionPipeline
from ingestion.ocr_engine import UniversalOCREngine

router = APIRouter(tags=["ingestion"])

# MarkItDown handles PDF/DOCX/PPTX/XLSX/HTML. Its import is lazy (inside `run`), so the
# package stays importable without it — see the `ocr` extra in pyproject.toml.
pipeline = IngestionPipeline(ocr_engine=UniversalOCREngine())


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    uploader_id: str = Form("unknown"),
    rbac_tags: str = Form("legal-team"),
) -> dict:
    """Upload a document and run it all the way to Postgres.

    `uploader_id` and `rbac_tags` are caller-supplied and unverified. There is no
    authentication anywhere in the system yet (§7, deferred — see WORK-SPLIT.md), so
    neither is a trust boundary; `RawFileStore` requires at least one tag regardless,
    because a document stored with no tag can never be access-gated later.
    """
    doc = pipeline.ingest(
        file_bytes=await file.read(),
        original_filename=file.filename or "upload",
        uploader_id=uploader_id,
        rbac_tags=[t.strip() for t in rbac_tags.split(",") if t.strip()],
    )
    chunks = pipeline.vector_store.get_chunks_for_document(doc.document_id)

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
    }
