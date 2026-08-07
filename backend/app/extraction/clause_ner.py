"""Chunks in, facts and candidate edges out.

Ambiguous candidates are *not* written to the KG here. They go to `app.ambiguity`, which
tries to resolve them before any human is involved.
"""

from __future__ import annotations

from app.schemas import CandidateEdge, Chunk, EdgeType, Fact, FactType, Provenance

from .provider import LLMProvider


def extract(chunks: list[Chunk], provider: LLMProvider) -> tuple[list[Fact], list[CandidateEdge]]:
    facts: list[Fact] = []
    candidates: list[CandidateEdge] = []

    for chunk in chunks:
        for i, raw in enumerate(provider.extract_facts(chunk), start=1):
            facts.append(
                Fact(
                    fact_id=f"{chunk.chunk_id}-F{i:02d}",
                    document_id=chunk.document_id,
                    clause_ref=chunk.clause_ref,
                    fact_type=FactType(raw["fact_type"]),
                    value=raw["value"],
                    # §7: bad OCR silently degrades everything downstream. A fact can
                    # never be more trustworthy than the page it was read off, so the
                    # page's OCR confidence caps the extraction confidence.
                    confidence=round(min(raw["confidence"], chunk.ocr_confidence), 4),
                    source_chunk_ids=[chunk.chunk_id],
                    provenance=Provenance.DIRECT_EXTRACTION,
                )
            )

        for i, raw in enumerate(provider.extract_edges(chunk), start=1):
            candidates.append(
                CandidateEdge(
                    edge_id=f"{chunk.chunk_id}-E{i:02d}",
                    document_id=chunk.document_id,
                    src_clause_ref=chunk.clause_ref,
                    dst_clause_ref=raw["dst_clause_ref"],
                    candidate_types=[EdgeType(t) for t in raw["candidate_types"]],
                    confidence=round(min(raw["confidence"], chunk.ocr_confidence), 4),
                    evidence_chunk_ids=[chunk.chunk_id],
                    pattern_key=raw.get("pattern_key"),
                )
            )

    return facts, candidates
