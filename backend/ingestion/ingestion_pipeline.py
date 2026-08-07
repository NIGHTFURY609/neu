"""
End-to-end orchestration for the Ingestion & Chunking stage (Dev 2):

    upload -> Raw Files store
           -> Metadata DB (document row)
           -> OCR (per-page, with confidence)
           -> Metadata DB (per-page OCR confidence)
           -> chunk
           -> embed
           -> Vector DB write
"""
from __future__ import annotations

import logging
from typing import List, Optional

from backend.ingestion.chunker import chunk_document
from backend.ingestion.embedding_service import EmbeddingService, StubEmbeddingService
from backend.ingestion.metadata_db import MetadataDB
from backend.ingestion.models import DocumentMetadata, DocumentStatus
from backend.ingestion.ocr_engine import OCREngine, StubOCREngine
from backend.ingestion.raw_file_store import RawFileStore
from backend.ingestion.vector_store import LocalJSONVectorStore, VectorStore

logger = logging.getLogger("ingestion_pipeline")


class IngestionPipeline:
    def __init__(
        self,
        raw_store: Optional[RawFileStore] = None,
        metadata_db: Optional[MetadataDB] = None,
        ocr_engine: Optional[OCREngine] = None,
        embedding_service: Optional[EmbeddingService] = None,
        vector_store: Optional[VectorStore] = None,
    ):
        self.raw_store = raw_store or RawFileStore()
        self.metadata_db = metadata_db or MetadataDB()
        self.ocr_engine = ocr_engine or StubOCREngine()
        self.embedding_service = embedding_service or StubEmbeddingService()
        self.vector_store = vector_store or LocalJSONVectorStore()

    def ingest(
        self,
        *,
        file_bytes: bytes,
        original_filename: str,
        uploader_id: str,
        rbac_tags: List[str],
    ) -> DocumentMetadata:
        saved = self.raw_store.save(
            file_bytes=file_bytes, original_filename=original_filename,
            uploader_id=uploader_id, rbac_tags=rbac_tags,
        )
        doc = DocumentMetadata(
            document_id=saved["document_id"], filename=original_filename,
            upload_timestamp=saved["upload_timestamp"], uploader_id=uploader_id,
            rbac_tags=rbac_tags, storage_path=saved["storage_path"],
            checksum_sha256=saved["checksum_sha256"], status=DocumentStatus.UPLOADED,
        )
        self.metadata_db.insert_document(doc)

        try:
            self.metadata_db.update_status(doc.document_id, DocumentStatus.OCR_IN_PROGRESS)
            pages = self.ocr_engine.run(doc.document_id, file_bytes)
            for page in pages:
                self.metadata_db.insert_page_confidence(page)
                if page.low_confidence:
                    logger.warning(
                        "low OCR confidence doc=%s page=%s conf=%.3f — "
                        "downstream extraction quality is at risk",
                        doc.document_id, page.page_number, page.confidence,
                    )

            self.metadata_db.update_status(doc.document_id, DocumentStatus.CHUNKING)
            chunks = chunk_document(pages, doc.document_id)

            self.metadata_db.update_status(doc.document_id, DocumentStatus.EMBEDDING)
            texts = [c.text for c in chunks]
            embeddings = self.embedding_service.embed(texts) if texts else []
            for c, vec in zip(chunks, embeddings):
                c.embedding = vec
                c.embedding_model = self.embedding_service.model_name

            self.vector_store.upsert_chunks(chunks)
            self.metadata_db.update_status(doc.document_id, DocumentStatus.INGESTED, page_count=len(pages))
        except Exception as exc:  # noqa: BLE001 — surface, don't swallow
            logger.exception("ingestion failed for document %s", doc.document_id)
            self.metadata_db.update_status(doc.document_id, DocumentStatus.FAILED, error=str(exc))
            raise

        return self.metadata_db.get_document(doc.document_id)  # type: ignore[return-value]