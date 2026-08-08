"""Chunks in, facts and candidate edges out.

Ambiguous candidates are *not* written to the KG here. They go to `app.ambiguity`, which
tries to resolve them before any human is involved.
"""

from __future__ import annotations

import re

from app.schemas import CandidateEdge, Chunk, EdgeType, Fact, FactType, Provenance

from .provider import LLMProvider

# Fact types whose `amount` the playbook compares numerically.
_MONETARY_FACT_TYPES = {FactType.LIABILITY_CAP, FactType.MONETARY_VALUE}
_NON_NUMERIC = re.compile(r"[^0-9.\-]")


def _coerce_money(fact_type: FactType, value: dict) -> dict:
    """Make `amount` numeric before it reaches the rules engine.

    Rules compare it numerically — `fixtures/playbook.sample.json` has
    `{"field": "amount", "operator": "lt", "value": 5000000}` — but the extractor yields
    the matched text: `provider.MockProvider.extract_facts` emits `money.group(0)`, i.e.
    the literal `"$1,000,000"`, and live mode is not guaranteed to do better either.

    `evaluator._numeric` refuses to coerce a str (deliberately — it never silently
    changes a type), so every liability-cap rule died as SYSTEM_ERROR, and
    `risk.pipeline.to_risk_flags` drops SYSTEM_ERROR records. The result was no flag, no
    redline and no error message: the rule looked like it had passed.

    Coerce here, once, at the boundary where the text is still the thing being read.
    The original is kept in `amount_text` so `redline/prompts.py` and the reviewer still
    see "$1,000,000" rather than 1000000.0. A string that will not parse is left exactly
    as it is, so SYSTEM_ERROR stays available as a truthful signal rather than becoming a
    silently wrong number.
    """
    if fact_type not in _MONETARY_FACT_TYPES:
        return value
    amount = value.get("amount")
    if not isinstance(amount, str):
        return value

    cleaned = _NON_NUMERIC.sub("", amount)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return value
    try:
        parsed = float(cleaned)
    except ValueError:
        return value

    coerced = dict(value)
    coerced["amount"] = int(parsed) if parsed.is_integer() else parsed
    coerced["amount_text"] = amount
    return coerced


def extract(chunks: list[Chunk], provider: LLMProvider) -> tuple[list[Fact], list[CandidateEdge]]:
    facts: list[Fact] = []
    candidates: list[CandidateEdge] = []

    for chunk in chunks:
        for i, raw in enumerate(provider.extract_facts(chunk), start=1):
            fact_type = FactType(raw["fact_type"])
            facts.append(
                Fact(
                    fact_id=f"{chunk.chunk_id}-F{i:02d}",
                    document_id=chunk.document_id,
                    clause_ref=chunk.clause_ref,
                    fact_type=fact_type,
                    value=_coerce_money(fact_type, raw["value"]),
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
