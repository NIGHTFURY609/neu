"""
Chunking: per-page OCR text -> overlapping chunks ready for embedding.

Two boundaries a chunk never crosses, for the same reason each time — a chunk that
straddles one has no single correct value for a field Dev 3 keys off:

  page   — `ocr_confidence` is inherited from the source page (§7 traceability)
  clause — `clause_ref` is what the Redline Generator resolves a flagged clause by

A chunk longer than one clause still gets exactly one `clause_ref`, and everything after
the heading it does not contain would be filed under the wrong section.
"""
from __future__ import annotations

from typing import Iterator, List, Tuple

from ingestion.clause_labeler import CLAUSE_HEADER, label_chunks
from ingestion.models import Chunk, PageOCRResult, new_id

DEFAULT_CHUNK_SIZE = 800     # characters
DEFAULT_CHUNK_OVERLAP = 150  # characters


def _clause_segments(text: str) -> Iterator[Tuple[int, int]]:
    """Half-open spans of `text`, cut at every clause heading.

    The first span is whatever precedes the first heading — a cover page or preamble,
    which `label_chunks` leaves unlabelled rather than guessing at.
    """
    bounds = sorted({0, len(text), *(m.start() for m in CLAUSE_HEADER.finditer(text))})
    for start, end in zip(bounds, bounds[1:]):
        if text[start:end].strip():
            yield start, end


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
    idx = start_index

    for segment_start, segment_end in _clause_segments(text):
        start = segment_start
        while start < segment_end:
            end = min(start + chunk_size, segment_end)
            piece = text[start:end]
            chunks.append(
                Chunk(
                    chunk_id=new_id(), document_id=document_id, page=page.page_number,
                    chunk_index=idx, text=piece, char_start=start, char_end=end,
                    ocr_confidence=page.confidence, low_confidence=page.low_confidence,
                    # Filled by `label_chunks` once the whole document is chunked — the
                    # carry-forward has to see the pages in order.
                    clause_ref="",
                )
            )
            idx += 1
            if end == segment_end:
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
    # Document-scoped, and only correct once every page is chunked and in order.
    return label_chunks(all_chunks)