"""Dev 2's output, into the database Dev 3 reads.

`metadata_db.py` stays exactly where it is. It is this stage's *private* pipeline-state
store — `status`, `storage_path`, `checksum_sha256`, per-page OCR confidence — and none
of those columns exist on Dev 3's `documents` table. This module is the other half: the
subset of that state that the rest of the system is contracted to see.

`merge()` with explicit field selection, mirroring `app.pipeline._persist` and
`app.redline.store.save_redlines`. Explicit rather than `**asdict(chunk)` because the two
`Chunk` shapes are compatible but not identical — `low_confidence`, `chunk_index` and
`embedding_model` are Dev 2's own and have no column here, and passing them through would
fail at the ORM rather than at this boundary.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from ingestion.models import Chunk, DocumentMetadata

logger = logging.getLogger("ingestion.postgres_sync")


def persist_document(
    doc: DocumentMetadata, chunks: List[Chunk], session: Optional[object] = None
) -> None:
    """Write the `documents` row and its `chunks` rows.

    Pass a `sqlalchemy.orm.Session` to join a caller's transaction; otherwise one is
    opened and committed here. Imports are function-local so `import ingestion` does not
    require a reachable database — the chunker and the fixture generator have no
    business connecting to one.
    """
    if session is not None:
        _write(session, doc, chunks)
        return

    from app.db import SessionLocal

    with SessionLocal() as owned:
        _write(owned, doc, chunks)
        owned.commit()

    logger.info(
        "synced document %s to Postgres (%d chunk(s))", doc.document_id, len(chunks)
    )


def _write(session, doc: DocumentMetadata, chunks: List[Chunk]) -> None:
    from app import models

    session.merge(
        models.Document(
            document_id=doc.document_id,
            filename=doc.filename,
            # Dev 2 carries RBAC tags as a list; the column is JSONB and Dev 3 writes a
            # dict. Stored as `{tag: true}` so both sides read the same shape.
            rbac_tags={tag: True for tag in doc.rbac_tags},
        )
    )
    for chunk in chunks:
        session.merge(
            models.Chunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                text=chunk.text,
                page=chunk.page,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                ocr_confidence=chunk.ocr_confidence,
                clause_ref=chunk.clause_ref,
                section_type=chunk.section_type,
                embedding=chunk.embedding,
            )
        )
