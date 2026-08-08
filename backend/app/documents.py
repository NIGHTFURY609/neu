"""Document listing, version lineage, processing control, and the original file.

The document list exists mostly to replace the free-text document-id input the dashboard
used to carry. It returns risk counts and open-escalation counts inline: the alternative
is the portfolio view fetching those per document in a loop, which is an N-request
waterfall that visibly stutters on any real number of documents.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.auth.deps import get_principal, require_principal
from app.auth.principal import Principal
from app.auth.rbac import authorize_document, visible_documents
from app.db import get_session
from app.processing import run_processing_job, to_status
from app.schemas import (
    DocKind,
    DocumentSummary,
    DocumentVersion,
    ProcessingState,
    ProcessingStatus,
)

router = APIRouter(tags=["documents"])


def _risk_counts(session: Session, document_ids: list[str]) -> dict[str, dict[str, int]]:
    """Severity histogram per document, for `flagged` rows only.

    Suppressed flags are excluded: a flag waived by a confirmed KG edge is not something
    a reviewer needs to act on, and counting it would make a well-papered contract look
    identical to an unreviewed one.
    """
    if not document_ids:
        return {}
    stmt = (
        select(models.RiskFlag.document_id, models.RiskFlag.severity, func.count())
        .where(
            models.RiskFlag.document_id.in_(document_ids),
            models.RiskFlag.status == "flagged",
        )
        .group_by(models.RiskFlag.document_id, models.RiskFlag.severity)
    )
    counts: dict[str, dict[str, int]] = {}
    for document_id, severity, total in session.execute(stmt):
        counts.setdefault(document_id, {})[severity] = total
    return counts


def _open_escalations(session: Session, document_ids: list[str]) -> dict[str, int]:
    if not document_ids:
        return {}
    stmt = (
        select(models.Escalation.document_id, func.count())
        .where(
            models.Escalation.document_id.in_(document_ids),
            models.Escalation.status == "pending_review",
        )
        .group_by(models.Escalation.document_id)
    )
    return {document_id: total for document_id, total in session.execute(stmt)}


def _to_summary(
    row: models.Document, risk: dict[str, dict[str, int]], escalations: dict[str, int]
) -> DocumentSummary:
    return DocumentSummary(
        document_id=row.document_id,
        filename=row.filename,
        title=row.title,
        doc_kind=DocKind(row.doc_kind),
        jurisdiction=row.jurisdiction,
        version=row.version,
        contract_family=row.contract_family,
        parent_document_id=row.parent_document_id,
        rbac_tags=list(row.rbac_tags or []),
        uploaded_at=row.uploaded_at,
        processing_status=ProcessingState(row.processing_status),
        risk_counts=risk.get(row.document_id, {}),
        open_escalations=escalations.get(row.document_id, 0),
    )


@router.get("/documents", response_model=list[DocumentSummary])
def list_documents(
    doc_kind: DocKind | None = Query(default=None),
    jurisdiction: str | None = Query(default=None),
    contract_family: str | None = Query(default=None),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[DocumentSummary]:
    stmt = select(models.Document).where(visible_documents(principal))
    if doc_kind is not None:
        stmt = stmt.where(models.Document.doc_kind == doc_kind.value)
    if jurisdiction is not None:
        stmt = stmt.where(models.Document.jurisdiction == jurisdiction)
    if contract_family is not None:
        stmt = stmt.where(models.Document.contract_family == contract_family)
    stmt = stmt.order_by(models.Document.uploaded_at.desc(), models.Document.document_id)

    rows = list(session.scalars(stmt))
    ids = [row.document_id for row in rows]
    risk = _risk_counts(session, ids)
    escalations = _open_escalations(session, ids)
    return [_to_summary(row, risk, escalations) for row in rows]


@router.get("/documents/{document_id}/versions", response_model=list[DocumentVersion])
def list_versions(
    document_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[DocumentVersion]:
    """Every version of this document's contract family, oldest first.

    Keyed on `contract_family` rather than walking `parent_document_id` upward: the walk
    needs a recursive CTE and still cannot see sibling branches, whereas the family is
    one indexed equality.
    """
    document = session.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, f"document {document_id} not found")
    authorize_document(session, document_id, principal)

    family = document.contract_family or document.document_id
    stmt = (
        select(models.Document)
        .where(models.Document.contract_family == family)
        .where(visible_documents(principal))
        .order_by(models.Document.version)
    )
    return [
        DocumentVersion(
            document_id=row.document_id,
            version=row.version,
            filename=row.filename,
            title=row.title,
            uploaded_at=row.uploaded_at,
            parent_document_id=row.parent_document_id,
            processing_status=ProcessingState(row.processing_status),
        )
        for row in session.scalars(stmt)
    ]


@router.get("/documents/{document_id}/status", response_model=ProcessingStatus)
def get_status(
    document_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> ProcessingStatus:
    document = session.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, f"document {document_id} not found")
    authorize_document(session, document_id, principal)
    return to_status(document)


@router.post("/documents/{document_id}/process", response_model=ProcessingStatus, status_code=202)
def start_processing(
    document_id: str,
    background: BackgroundTasks,
    force: bool = Query(default=False, description="Re-run a document that already finished."),
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_principal),
) -> ProcessingStatus:
    """Queue the Clause NER -> Risk -> Redline chain. Returns immediately with 202.

    409 rather than a silent second run when one is already in flight: the stages write
    through `session.merge`, so two concurrent runs would interleave commits on the same
    rows and the last writer would win at random.
    """
    document = session.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, f"document {document_id} not found")
    authorize_document(session, document_id, principal)

    if document.processing_status in {ProcessingState.QUEUED.value, ProcessingState.RUNNING.value} and not force:
        raise HTTPException(409, f"{document_id} is already {document.processing_status}")

    document.processing_status = ProcessingState.QUEUED.value
    document.processing_stage = None
    document.processing_error = None
    document.processing_started_at = None
    document.processing_finished_at = None
    session.commit()

    # Only the id crosses into the task — see run_processing_job's docstring for why
    # handing it this request's session would be a bug.
    background.add_task(run_processing_job, document_id)
    return to_status(document)


@router.get("/documents/{document_id}/file")
def download_original(
    document_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_principal),
) -> Response:
    """The original uploaded bytes, gated on RBAC.

    `RawFileStore.read` has enforced tag overlap since it was written but had no callers,
    so the one piece of content-level access control in the repo was unreachable. This is
    that caller. The check is made twice on purpose — once against the row, once inside
    the store — because they fail differently: the row check catches a document you may
    not see, the store check catches a file whose tags have drifted from its metadata.
    """
    document = authorize_document(session, document_id, principal)
    if document is None:  # superuser fast path skipped the fetch
        document = session.get(models.Document, document_id)
        if document is None:
            raise HTTPException(404, f"document {document_id} not found")

    # Reuse the pipeline's own store so the configured root matches the one that wrote
    # the file; constructing a second RawFileStore() here would silently resolve a
    # different directory under a different working directory.
    from ingestion.api import pipeline as ingestion_pipeline

    tags = list(document.rbac_tags or [])
    try:
        data = ingestion_pipeline.raw_store.read(
            document_id,
            requester_rbac_tags=(
                tags if principal.is_superuser else sorted(principal.rbac_tags)
            ),
            required_tags=tags,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, f"no stored file for document {document_id}") from exc

    return Response(
        data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )
