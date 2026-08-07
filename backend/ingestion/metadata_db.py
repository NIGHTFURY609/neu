"""
Metadata DB writes owned by Dev 2: Document_ID, upload timestamp, RBAC tags,
plus per-page OCR confidence (§7). SQLite for now — swap the DSN for
Postgres later without touching callers.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator, List, Optional

from backend.ingestion.models import DocumentMetadata, DocumentStatus, PageOCRResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    upload_timestamp TEXT NOT NULL,
    uploader_id TEXT NOT NULL,
    rbac_tags TEXT NOT NULL,        -- JSON list
    storage_path TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    page_count INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS page_ocr_confidence (
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    confidence REAL NOT NULL,
    low_confidence INTEGER NOT NULL,
    engine TEXT NOT NULL,
    PRIMARY KEY (document_id, page_number),
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
);
"""


class MetadataDB:
    def __init__(self, path: str = "./metadata.db"):
        self.path = path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- documents ---------------------------------------------------

    def insert_document(self, doc: DocumentMetadata) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO documents
                   (document_id, filename, upload_timestamp, uploader_id,
                    rbac_tags, storage_path, checksum_sha256, status,
                    page_count, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc.document_id, doc.filename, doc.upload_timestamp,
                    doc.uploader_id, json.dumps(doc.rbac_tags),
                    doc.storage_path, doc.checksum_sha256, doc.status.value,
                    doc.page_count, doc.error,
                ),
            )

    def update_status(
        self, document_id: str, status: DocumentStatus,
        page_count: Optional[int] = None, error: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE documents SET status = ?,
                   page_count = COALESCE(?, page_count),
                   error = ?
                   WHERE document_id = ?""",
                (status.value, page_count, error, document_id),
            )

    def get_document(self, document_id: str) -> Optional[DocumentMetadata]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        if row is None:
            return None
        return DocumentMetadata(
            document_id=row["document_id"], filename=row["filename"],
            upload_timestamp=row["upload_timestamp"], uploader_id=row["uploader_id"],
            rbac_tags=json.loads(row["rbac_tags"]), storage_path=row["storage_path"],
            checksum_sha256=row["checksum_sha256"], status=DocumentStatus(row["status"]),
            page_count=row["page_count"], error=row["error"],
        )

    # -- per-page OCR confidence (§7) ---------------------------------

    def insert_page_confidence(self, result: PageOCRResult) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO page_ocr_confidence
                   (document_id, page_number, confidence, low_confidence, engine)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    result.document_id, result.page_number, result.confidence,
                    int(result.low_confidence), result.engine,
                ),
            )

    def get_low_confidence_pages(self, document_id: str) -> List[dict]:
        """The query a dashboard or ops alert calls to surface OCR failures
        that would otherwise stay silent."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT page_number, confidence FROM page_ocr_confidence
                   WHERE document_id = ? AND low_confidence = 1
                   ORDER BY page_number""",
                (document_id,),
            ).fetchall()
        return [{"page_number": r["page_number"], "confidence": r["confidence"]} for r in rows]