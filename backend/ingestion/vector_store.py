"""
Vector DB writes. Local JSON-backed store so integration can start before
a real vector DB (pgvector / Pinecone / Chroma) is provisioned. Dev 3 codes
against this interface, so swapping the backend later doesn't ripple downstream.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from backend.ingestion.models import Chunk


class VectorStore:
    def upsert_chunks(self, chunks: List[Chunk]) -> None:
        raise NotImplementedError

    def get_chunks_for_document(self, document_id: str) -> List[Chunk]:
        raise NotImplementedError


class LocalJSONVectorStore(VectorStore):
    """Dev/test backend. One JSON file per document under `root`."""

    def __init__(self, root: str = "./vector_db"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, document_id: str) -> Path:
        return self.root / f"{document_id}.json"

    def upsert_chunks(self, chunks: List[Chunk]) -> None:
        by_doc: dict[str, List[Chunk]] = {}
        for c in chunks:
            by_doc.setdefault(c.document_id, []).append(c)

        for document_id, doc_chunks in by_doc.items():
            path = self._path(document_id)
            existing = {}
            if path.exists():
                existing = {c["chunk_id"]: c for c in json.loads(path.read_text())}
            for c in doc_chunks:
                existing[c.chunk_id] = asdict(c)
            path.write_text(json.dumps(list(existing.values()), indent=2))

    def get_chunks_for_document(self, document_id: str) -> List[Chunk]:
        path = self._path(document_id)
        if not path.exists():
            return []
        raw = json.loads(path.read_text())
        return [Chunk(**r) for r in raw]