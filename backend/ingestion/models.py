"""Shared data models for the Ingestion & Chunking stage (Dev 2 scope)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

LOW_OCR_CONFIDENCE_THRESHOLD = 0.75


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    OCR_IN_PROGRESS = "ocr_in_progress"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INGESTED = "ingested"
    FAILED = "failed"


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4()}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DocumentMetadata:
    """Row shape for the Metadata DB `documents` table."""
    document_id: str
    filename: str
    upload_timestamp: str
    uploader_id: str
    rbac_tags: List[str]
    storage_path: str
    checksum_sha256: str
    status: DocumentStatus = DocumentStatus.UPLOADED
    page_count: Optional[int] = None
    error: Optional[str] = None


@dataclass
class PageOCRResult:
    """Per-page OCR output. §7: bad OCR is a silent failure that degrades
    every downstream stage — confidence must be tracked and exposed, never
    swallowed."""
    document_id: str
    page_number: int
    text: str
    confidence: float  # 0.0 - 1.0
    engine: str = "stub-ocr"
    low_confidence: bool = field(init=False)

    def __post_init__(self):
        self.low_confidence = self.confidence < LOW_OCR_CONFIDENCE_THRESHOLD


@dataclass
class Chunk:
    """Row shape for the Vector DB `chunks` collection."""
    chunk_id: str
    document_id: str
    page_number: int
    chunk_index: int
    text: str
    char_start: int
    char_end: int
    ocr_confidence: float
    low_confidence: bool
    embedding: Optional[List[float]] = None
    embedding_model: Optional[str] = None