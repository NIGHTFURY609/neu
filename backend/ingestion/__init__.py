"""Ingestion & Chunking stage (Dev 2).

Imports are bare `ingestion.X`, rooted at `backend/`, the same root `app.X` uses. The
two packages have to be importable from one place because `ingestion.postgres_sync`
writes through `app.models`.
"""
