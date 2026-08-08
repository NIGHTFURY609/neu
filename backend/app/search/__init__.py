"""Lexical search over chunks and regulations.

Deliberately not vector search. `ingestion/embedding_service.py` defaults to
`StubEmbeddingService`, which produces 1536 floats by iterated SHA-256 — a stable hash,
carrying no semantic signal whatsoever — and `LocalJSONVectorStore` has no similarity
method at all. Turning on "semantic search" over that data would return confident
nonsense, so this ranks lexically and leaves a seam for real embeddings.

Two backends implement one Protocol:

    postgres_fts.py  production. tsvector + GIN + ts_rank_cd, all filtering in SQL.
    memory_bm25.py   tests and offline use. Genuine Okapi BM25.

`ts_rank_cd` is cover density over weighted lexemes, without IDF — it is not BM25, and
the class is named `PostgresFTSBackend` rather than `BM25Backend` so nobody later reads
the codebase as claiming otherwise.
"""
