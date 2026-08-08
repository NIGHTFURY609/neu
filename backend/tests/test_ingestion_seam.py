"""The Dev 2 -> Dev 3 seam, exercised end to end without a database.

Every other test in this suite starts from `fixtures/chunks.sample.json`, which is
hand-authored. That proves the Redline Generator works on the *proposed* chunk shape and
says nothing about the shape Dev 2's chunker actually emits. This file starts from the
chunker instead, so a chunk field that goes missing upstream fails here rather than
silently producing an empty redline set at integration.

`test_dev2_output_produces_a_real_redline` is the one that matters. Its counterpart,
`test_chunks_without_clause_refs_produce_no_redline`, pins the failure mode it was
written against: with no `clause_ref`, `grounding.assess` can never resolve the flagged
clause, so every flag escalates and Stage 4 produces nothing at all.
"""

from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import require_principal
from app.auth.principal import Principal
from app.redline.generator import run
from app.redline.pipeline import build_context
from app.schemas import (
    Chunk,
    EscalationReason,
    PlaybookRule,
    RiskFlag,
    RiskFlagStatus,
    RiskSeverity,
)
from ingestion import api as ingestion_api
from ingestion import ingestion_pipeline as pipeline_module
from ingestion.chunker import chunk_document
from ingestion.ingestion_pipeline import IngestionPipeline
from ingestion.metadata_db import MetadataDB
from ingestion.ocr_engine import StubOCREngine
from ingestion.raw_file_store import RawFileStore
from ingestion.vector_store import LocalJSONVectorStore

DOCUMENT = """\
1.1 Definitions

"Excluded Claims" means claims arising from data loss, third-party indemnity demands, or
regulatory fines. For the avoidance of doubt, Excluded Claims are carved out of the scope
of the limitation of liability in Section 2.2 and are not released, waived, or discharged
by any provision of this Agreement.

2.2 Limitation of Liability

Each party's total aggregate liability arising out of or related to this Agreement shall
not exceed one million dollars ($1,000,000).

4.1 Precedence

Notwithstanding Section 2.2, the liability cap set forth in this Section 4.1 shall control
with respect to any claim brought under the Security Addendum.
"""

RULE = PlaybookRule(
    rule_id="RULE-LIAB-CAP",
    version=1,
    clause_pattern="limitation of liability",
    severity=RiskSeverity.HIGH,
    rationale="An uncapped or under-capped liability ceiling is not acceptable.",
    standard_language=(
        "Each party's total aggregate liability shall not exceed the fees paid in the "
        "twelve (12) months preceding the claim."
    ),
)


def ingest(text: str, document_id: str = "SEAM-001") -> list[Chunk]:
    """Dev 2's pipeline, minus the stores: OCR -> chunk -> Dev 3's input contract.

    The `model_validate(asdict(...))` is the point of the test. It only works while the
    two `Chunk` shapes agree field-for-field, which is exactly the seam that was broken.
    """
    pages = StubOCREngine().run(document_id, text.encode("utf-8"))
    return [Chunk.model_validate(asdict(c)) for c in chunk_document(pages, document_id)]


def flag(clause_ref: str = "2.2", document_id: str = "SEAM-001") -> RiskFlag:
    return RiskFlag(
        id="RISK-001",
        document_id=document_id,
        clause_ref=clause_ref,
        rule_id=RULE.rule_id,
        rule_version=RULE.version,
        severity=RiskSeverity.HIGH,
        status=RiskFlagStatus.FLAGGED,
    )


def test_dev2_output_produces_a_real_redline():
    """Real ingestion output through Stage 4, with nothing hand-authored in between."""
    chunks = ingest(DOCUMENT)
    ctx = build_context(chunks=chunks, confirmed_edges=[], facts=[], pending_edges=[])

    result = run([flag()], [RULE], ctx)

    assert result.redlines, "Dev 2's chunks grounded no redline at all"
    redline = result.redlines[0]
    assert redline.clause_ref == "2.2"
    assert redline.grounding_chunk_ids


def test_chunker_labels_every_chunk_with_the_clause_it_belongs_to():
    chunks = ingest(DOCUMENT)

    assert {c.clause_ref for c in chunks} <= {"1.1", "2.2", "4.1"}
    assert all(c.clause_ref for c in chunks), "a chunk reached Dev 3 with no clause label"
    # The glossary the retrieval loop reads is built from `section_type == "definitions"`.
    assert any(c.section_type == "definitions" for c in chunks)


def test_the_definition_is_reachable_by_term_not_just_by_ref():
    ctx = build_context(
        chunks=ingest(DOCUMENT), confirmed_edges=[], facts=[], pending_edges=[]
    )

    assert ctx.definition_of("Excluded Claims") is not None


@pytest.mark.parametrize("missing", ["clause_ref"])
def test_chunks_without_clause_refs_produce_no_redline(missing):
    """The regression this file exists for.

    `grounding.assess` early-returns "retrieve the flagged clause" when
    `ctx.clause(risk.clause_ref)` is None, and no query can ever close that gap, so the
    loop burns its whole budget and escalates. Zero redlines, no error — the silent
    failure that made Stage 4 look like it worked.
    """
    unlabelled = [c.model_copy(update={missing: ""}) for c in ingest(DOCUMENT)]
    ctx = build_context(
        chunks=unlabelled, confirmed_edges=[], facts=[], pending_edges=[]
    )

    result = run([flag()], [RULE], ctx)

    assert result.redlines == []
    assert [r.reason for _, _, r in result.escalations] == [
        EscalationReason.BUDGET_EXHAUSTED
    ]


# ------------------------------------------------------------------- the upload route


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    """`/ingest/upload` against local stores, with the Postgres write recorded.

    Everything is redirected into `tmp_path` except the Postgres sync, which is captured
    instead of stubbed out — the defect this pins is that `/upload` used to call the OCR
    engine directly and persist nothing at all, so "was persist_document reached, and
    with how many chunks" *is* the assertion.
    """
    from app import api

    synced: list[tuple] = []
    monkeypatch.setattr(
        pipeline_module,
        "persist_document",
        lambda doc, chunks: synced.append((doc, chunks)),
    )
    monkeypatch.setattr(
        ingestion_api,
        "pipeline",
        IngestionPipeline(
            raw_store=RawFileStore(root=str(tmp_path / "raw")),
            metadata_db=MetadataDB(path=str(tmp_path / "metadata.db")),
            ocr_engine=StubOCREngine(),
            vector_store=LocalJSONVectorStore(root=str(tmp_path / "vector")),
        ),
    )
    api.app.dependency_overrides[require_principal] = lambda: Principal(user_id="uploader-1")
    return TestClient(api.app), synced


def test_upload_runs_the_whole_pipeline_not_just_ocr(upload_client):
    http, synced = upload_client

    response = http.post(
        "/ingest/upload",
        files={"file": ("contract.txt", DOCUMENT.encode("utf-8"), "text/plain")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ingested"
    assert body["chunks"] > 0

    assert len(synced) == 1, "the upload never reached Postgres"
    doc, chunks = synced[0]
    assert doc.document_id == body["document_id"]
    assert len(chunks) == body["chunks"]
    assert all(c.clause_ref for c in chunks), "chunks reached Postgres unlabelled"
