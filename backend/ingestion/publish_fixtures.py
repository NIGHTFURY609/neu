"""
Publishes the two fixtures Dev 3 is blocked on:
  1. document_id_fixture.json       — sample Document_ID + metadata shape
  2. vector_db_chunk_fixture.json   — sample chunks with embeddings

Run this against a couple of representative sample contracts as soon as the
pipeline runs end to end — republish whenever the schema changes.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from backend.ingestion.ingestion_pipeline import IngestionPipeline

SAMPLE_DOCS_DIR = Path("./sample_docs")
FIXTURES_DIR = Path("./fixtures")


def publish(sample_filenames: list[str] | None = None) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    pipeline = IngestionPipeline()

    filenames = sample_filenames or _discover_samples()
    document_fixtures = []
    all_chunks = []

    for filename in filenames:
        path = SAMPLE_DOCS_DIR / filename
        file_bytes = path.read_bytes()
        doc = pipeline.ingest(
            file_bytes=file_bytes, original_filename=filename,
            uploader_id="fixture-generator", rbac_tags=["legal-team"],
        )
        fixture_row = asdict(doc)
        fixture_row["status"] = doc.status.value  # enum -> plain string for JSON
        document_fixtures.append(fixture_row)
        all_chunks.extend(pipeline.vector_store.get_chunks_for_document(doc.document_id))

    (FIXTURES_DIR / "document_id_fixture.json").write_text(json.dumps(document_fixtures, indent=2))
    (FIXTURES_DIR / "vector_db_chunk_fixture.json").write_text(
        json.dumps([asdict(c) for c in all_chunks], indent=2)
    )
    print(f"published {len(document_fixtures)} document(s), {len(all_chunks)} chunk(s) to {FIXTURES_DIR}/")


def _discover_samples() -> list[str]:
    if not SAMPLE_DOCS_DIR.exists():
        raise FileNotFoundError(f"{SAMPLE_DOCS_DIR} not found — drop sample contracts there first")
    return [p.name for p in SAMPLE_DOCS_DIR.iterdir() if p.is_file()]


if __name__ == "__main__":
    publish()