"""Running the post-ingest chain off the request thread.

`ingestion.api.upload_document` used to call `orchestration.process_document` inline, so
an upload request stayed open for the whole Clause NER -> Risk -> Redline chain. In
`LLM_MODE=mock` that is fast enough to hide the problem; in `live` mode the browser sits
on a spinner for minutes and any failure is logged where nobody looks. The upload now
records the document and queues this, and the UI polls `GET /documents/{id}/status`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime

from app import models, orchestration
from app.kg import store as kg_store
from app.schemas import ProcessingState, ProcessingStatus

logger = logging.getLogger("app.processing")


def to_status(document: models.Document) -> ProcessingStatus:
    return ProcessingStatus(
        document_id=document.document_id,
        status=ProcessingState(document.processing_status),
        stage=document.processing_stage,
        error=document.processing_error,
        started_at=document.processing_started_at,
        finished_at=document.processing_finished_at,
        summary=dict(document.processing_summary or {}),
    )


def _fail(session, document: models.Document, stage: str | None, message: str) -> None:
    document.processing_status = ProcessingState.FAILED.value
    document.processing_stage = stage
    document.processing_error = message
    document.processing_finished_at = datetime.now(UTC)
    session.commit()


def _fail_in_new_session(document_id: str, message: str) -> None:
    """Record a failure on a connection that is definitely usable.

    Separate session on purpose: if the job died mid-transaction the original one is
    poisoned, and every write on it — including the one recording the failure — is
    silently discarded at rollback. That would leave the document stuck on `running`
    forever, which is the worst of the possible end states because the UI polls it.
    """
    from app.db import SessionLocal

    try:
        with SessionLocal() as session:
            document = session.get(models.Document, document_id)
            if document is not None:
                _fail(session, document, document.processing_stage, message)
    except Exception:
        logger.exception("could not record the failure of %s", document_id)


def run_processing_job(document_id: str) -> None:
    """Background entry point for one document.

    Takes only a document id — never a Session, an ORM instance, or a Principal. Since
    FastAPI 0.106 a `yield` dependency's teardown runs *before* the response is sent
    while background tasks run *after*, so a task handed the request's session gets one
    that is already closed. That surfaces as a DetachedInstanceError in a worker thread,
    minutes after a request that returned 202, and no test with a mocked store would
    catch it. Opening our own session is the only safe option.

    Declared `def`, not `async def`: Starlette runs a sync background task in a
    threadpool, so the blocking psycopg and Anthropic calls inside cannot stall the event
    loop. As `async def` this would freeze every other request for the length of a live
    LLM run.
    """
    from app.db import SessionLocal

    try:
        with SessionLocal() as session:
            document = session.get(models.Document, document_id)
            if document is None:
                logger.warning("no document %s to process", document_id)
                return

            document.processing_status = ProcessingState.RUNNING.value
            document.processing_stage = "clause_ner"
            document.processing_started_at = datetime.now(UTC)
            document.processing_finished_at = None
            document.processing_error = None
            session.commit()

            chunks = kg_store.list_chunks(session, document_id)
            if not chunks:
                # Ingestion succeeded but produced nothing readable — a scanned page that
                # OCR'd to noise, say. Downstream would report zero facts, which reads as
                # "clean document" rather than "we could not read it".
                _fail(session, document, "ingestion", "no chunks stored for this document")
                return

            summary = orchestration.process_document(
                session, document_id, chunks, jurisdiction=document.jurisdiction
            )

            # Re-fetch: process_document commits three times on its way through.
            document = session.get(models.Document, document_id)
            if document is None:
                return
            if summary.error:
                _fail(session, document, summary.stage, summary.error)
                return

            document.processing_status = ProcessingState.SUCCEEDED.value
            document.processing_stage = None
            document.processing_summary = asdict(summary)
            document.processing_finished_at = datetime.now(UTC)
            session.commit()
    except Exception as exc:
        # Mandatory. TestClient executes background tasks synchronously and re-raises
        # them into the originating request, so an unguarded throw here would turn an
        # upload test red for a reason that has nothing to do with uploading.
        logger.exception("processing job crashed for %s", document_id)
        _fail_in_new_session(document_id, f"processing crashed: {exc}")
