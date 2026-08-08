import json
from pathlib import Path

import pytest

from app.redline.pipeline import build_context
from app.schemas import Chunk, Fact, KGEdge, PlaybookRule, RiskFlag

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def sample_document() -> dict:
    return load("chunks.sample.json")


@pytest.fixture
def sample_chunks(sample_document) -> list[Chunk]:
    return [Chunk.model_validate(c) for c in sample_document["chunks"]]


@pytest.fixture
def sample_risks() -> list[RiskFlag]:
    return [RiskFlag.model_validate(r) for r in load("risk.sample.json")]


@pytest.fixture
def sample_playbook() -> list[PlaybookRule]:
    return [PlaybookRule.model_validate(r) for r in load("playbook.sample.json")]


@pytest.fixture
def retrieval_context(sample_chunks):
    """What the Redline Generator is allowed to read, built the way the CLI builds it.

    Through `build_context` on purpose: the confirmed/pending split is the §3.3 rule this
    stage most needs to get right, and a test that assembled the context by hand could
    pass while the real entry point leaked pending edges.
    """
    return build_context(
        chunks=sample_chunks,
        confirmed_edges=[KGEdge.model_validate(e) for e in load("kg_edges.confirmed.json")],
        facts=[Fact.model_validate(f) for f in load("facts.sample.json")],
        pending_edges=[KGEdge.model_validate(e) for e in load("kg_edges.pending_review.json")],
    )
