"""
Embedding generation. Provider-agnostic interface; StubEmbeddingService gives
deterministic vectors for local dev/fixtures without an API key or network
call, since Dev 2 is a zero-mock root stage.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import List

EMBEDDING_DIM = 384


class EmbeddingService(ABC):
    model_name: str

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        ...


class StubEmbeddingService(EmbeddingService):
    model_name = "stub-embed-v1"

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]

    @staticmethod
    def _embed_one(text: str) -> List[float]:
        vec: List[float] = []
        seed = text.encode("utf-8")
        while len(vec) < EMBEDDING_DIM:
            seed = hashlib.sha256(seed).digest()
            vec.extend((b / 127.5 - 1.0) for b in seed)
        return [round(v, 6) for v in vec[:EMBEDDING_DIM]]


class RealEmbeddingService(EmbeddingService):
    """
    Placeholder adapter for a real embedding API. Swap StubEmbeddingService
    for this in ingestion_pipeline.py once credentials/infra are wired up —
    no other module needs to change.
    """
    model_name = "text-embedding-3-small"

    def __init__(self, client):
        self.client = client

    def embed(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(model=self.model_name, input=texts)
        return [d.embedding for d in response.data]