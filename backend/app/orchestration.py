"""Chains Clause NER -> Risk Engine -> Redline Generator against real data.

Before this, `app.pipeline`, `app.risk.pipeline` and `app.redline.pipeline` only ever ran
by hand from their own CLIs, each reading its input from a JSON fixture file (see their
`main()` functions) — never from the database rows the previous stage actually wrote.
`ingestion.api.upload_document` writes real chunks (the Vector DB, §2) to Postgres, but
nothing ever read them back: `ingestion/embedding_service.py` says as much ("nothing
downstream reads embeddings numerically yet"). This module is that missing read, chained
across all three stages so one upload produces facts, risk flags and redlines instead of
requiring three manual `--persist` runs against hand-authored fixtures.

Called by `ingestion.api.upload_document` right after chunks are persisted. Each stage
commits its own output before the next stage runs — a failure partway through is logged
and stops the chain there. It does not roll back whatever an earlier stage already
committed, and it never undoes the ingestion that already succeeded: an upload is real
whether or not the pipeline behind it finishes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import pipeline as clause_ner_pipeline
from app.kg import store as kg_store
from app.playbook_store import load_active_playbook
from app.redline import generator as redline_generator
from app.redline import store as redline_store
from app.redline.retrieval import RetrievalContext
from app.risk import pipeline as risk_pipeline
from app.risk import store as risk_store
from app.schemas import Chunk

logger = logging.getLogger("app.orchestration")


@dataclass
class PipelineSummary:
    facts: int = 0
    confirmed_edges: int = 0
    pending_edges: int = 0
    risk_flags: int = 0
    redlines: int = 0
    escalations: int = 0
    # `stage` is the last stage entered; `error` is None only when every stage completed.
    # Both exist because this function returns a partial summary on failure rather than
    # raising, so without them a caller cannot tell a document with genuinely zero facts
    # from one whose extraction blew up — and `app.processing` would record a crashed
    # run as `succeeded`.
    stage: str = "done"
    error: str | None = None


def process_document(
    session: Session,
    document_id: str,
    chunks: list[Chunk],
    jurisdiction: str | None = None,
) -> PipelineSummary:
    """Run Clause NER, then Risk, then Redline, for one just-ingested document.

    `jurisdiction` narrows the playbook to rules that apply there plus rules that apply
    everywhere. None means "every active rule", which is the pre-0004 behaviour.
    """
    summary = PipelineSummary()

    summary.stage = "clause_ner"
    try:
        ner_result = clause_ner_pipeline.run(chunks)
    except Exception as exc:
        logger.exception("Clause NER failed for %s; nothing to run risk/redline on", document_id)
        summary.error = f"clause NER failed: {exc}"
        return summary

    kg_store.save_facts(session, ner_result.facts)
    kg_store.save_edges(session, ner_result.edges)
    kg_store.save_escalations(session, ner_result.escalations)
    session.commit()
    summary.facts = len(ner_result.facts)
    summary.confirmed_edges = len(ner_result.confirmed_edges)
    summary.pending_edges = len(ner_result.pending_edges)
    summary.escalations += len(ner_result.escalations)

    summary.stage = "playbook"
    try:
        risk_rules, playbook_rules = load_active_playbook(session, jurisdiction)
    except Exception as exc:
        logger.exception("could not load the active playbook; stopping before risk assessment for %s", document_id)
        summary.error = f"could not load the playbook: {exc}"
        return summary
    if not risk_rules:
        # Not an error: a document can legitimately be processed before anyone has
        # seeded a playbook. The stage is recorded so the UI can say why there are no
        # risk flags rather than implying the document is clean.
        logger.warning("no active playbook rules; skipping risk assessment for %s", document_id)
        summary.stage = "risk"
        return summary

    summary.stage = "risk"
    try:
        records = risk_pipeline.run_risk_assessment(
            document_id, risk_rules, ner_result.facts, ner_result.confirmed_edges
        )
        flags = risk_pipeline.to_risk_flags(document_id, records, risk_rules, ner_result.facts)
    except Exception as exc:
        logger.exception("risk assessment failed for %s", document_id)
        summary.error = f"risk assessment failed: {exc}"
        return summary

    risk_store.save_risk_flags(session, flags)
    session.commit()
    summary.risk_flags = len(flags)

    summary.stage = "redline"
    try:
        ctx = RetrievalContext(
            chunks=chunks,
            confirmed_edges=ner_result.confirmed_edges,
            facts=ner_result.facts,
            # Refs only — same reasoning as app.redline.pipeline.build_context: the loop
            # learns that the pair is unsettled, never what the unconfirmed edge claimed.
            unresolved_pairs=[(e.src_clause_ref, e.dst_clause_ref) for e in ner_result.pending_edges],
        )
        generation = redline_generator.run(flags, playbook_rules, ctx)
    except Exception as exc:
        logger.exception("redline generation failed for %s", document_id)
        summary.error = f"redline generation failed: {exc}"
        return summary

    redline_store.save_redlines(session, generation.redlines)
    redline_store.save_redline_escalations(session, generation.escalations)
    session.commit()
    summary.redlines = len(generation.redlines)
    summary.escalations += len(generation.escalations)

    summary.stage = "done"
    return summary
