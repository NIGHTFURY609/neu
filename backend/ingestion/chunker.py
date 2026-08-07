"""
Chunking: per-page OCR text -> overlapping chunks ready for embedding.
Chunks never cross a page boundary, so a chunk's ocr_confidence is always
unambiguous (it inherits its source page's confidence — §7 traceability).
"""
from __future__ import annotations

from typing import List

from backend.ingestion.models import Chunk, PageOCRResult, new_id

DEFAULT_CHUNK_SIZE = 800     # characters
DEFAULT_CHUNK_OVERLAP = 150  # characters


def chunk_page(
    page: PageOCRResult,
    document_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    start_index: int = 0,
) -> List[Chunk]:
    text = page.text
    if not text:
        return []
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: List[Chunk] = []
    start = 0
    idx = start_index
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        piece = text[start:end]
        chunks.append(
            Chunk(
                chunk_id=new_id(), document_id=document_id, page_number=page.page_number,
                chunk_index=idx, text=piece, char_start=start, char_end=end,
                ocr_confidence=page.confidence, low_confidence=page.low_confidence,
            )
        )
        idx += 1
        if end == text_len:
            break
        start = end - overlap

    return chunks


def chunk_document(
    pages: List[PageOCRResult],
    document_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Chunk]:
    all_chunks: List[Chunk] = []
    running_index = 0
    for page in sorted(pages, key=lambda p: p.page_number):
        page_chunks = chunk_page(page, document_id, chunk_size, overlap, start_index=running_index)
        all_chunks.extend(page_chunks)
        running_index += len(page_chunks)
    return all_chunks