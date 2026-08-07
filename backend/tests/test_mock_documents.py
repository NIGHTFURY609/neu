"""The three mock documents under fixtures/mock/.

They exist to show the stage generalises past the document its mock rules were written
around, so what is pinned here is the *distribution* of outcomes, not exact confidences:
one document where the retry loop mostly fails, one where OCR quality is the only thing
standing in its way, and one where nothing reaches a human at all. If a change to the
strategies quietly collapses all three into the same shape, this fails.
"""

import json

import pytest

from app.pipeline import run
from app.schemas import Chunk, Provenance

from .conftest import FIXTURES


def load(name: str) -> list[Chunk]:
    payload = json.loads((FIXTURES / "mock" / name).read_text(encoding="utf-8"))
    return [Chunk.model_validate(c) for c in payload["chunks"]]


@pytest.mark.parametrize(
    "name,confirmed,escalated",
    [
        ("chunks.doc-002.json", 1, 2),  # no precedent available anywhere
        ("chunks.doc-003.json", 1, 2),  # same clauses as DOC-001, off a bad scan
        ("chunks.doc-004.json", 3, 0),  # clean amendment, empty review queue
    ],
)
def test_mock_document_outcomes(name, confirmed, escalated):
    result = run(load(name))

    assert len(result.confirmed_edges) == confirmed
    assert len(result.escalations) == escalated
    assert len(result.pending_edges) == escalated


def test_bad_ocr_is_the_only_reason_doc_003_escalates():
    """DOC-003's clauses resolve on a clean page; only the scan quality stops them.

    Re-running the same text at full OCR confidence has to empty the queue — otherwise
    the escalations are being caused by something else and the document is not
    demonstrating what it claims to.
    """
    chunks = load("chunks.doc-003.json")
    clean = [c.model_copy(update={"ocr_confidence": 0.99}) for c in chunks]

    assert len(run(chunks).escalations) == 2
    assert run(clean).escalations == []


def test_doc_004_records_how_each_edge_was_confirmed():
    """§4.2 — a reviewer must be able to tell retry-resolved edges from direct ones."""
    by_ref = {e.src_clause_ref: e.resolved_by for e in run(load("chunks.doc-004.json")).edges}

    assert by_ref == {
        "4.1": Provenance.DIRECT_EXTRACTION,
        "5.1": Provenance.RETRY_KG_PRECEDENT,
        "6.1": Provenance.RETRY_WIDEN_CONTEXT,
    }
