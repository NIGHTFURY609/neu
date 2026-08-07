import json
from pathlib import Path

import pytest

from app.schemas import Chunk

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def sample_document() -> dict:
    return json.loads((FIXTURES / "chunks.sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def sample_chunks(sample_document) -> list[Chunk]:
    return [Chunk.model_validate(c) for c in sample_document["chunks"]]
