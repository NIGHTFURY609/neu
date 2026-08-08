"""Run the Clause NER + KG stage over a document.

    chunks -> facts + candidate edges -> bounded retry -> confirmed edges | escalations

Runnable with or without a database. `--persist` writes to the Metadata DB; without it
the stage runs purely in memory, which is how the published fixtures are regenerated.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.ambiguity.resolver import resolve
from app.ambiguity.strategies import ResolutionContext
from app.escalation import confirm, escalate
from app.extraction import clause_ner
from app.extraction.provider import get_provider
from app.schemas import (
    CandidateEdge,
    Chunk,
    EscalationRecord,
    Fact,
    KGEdge,
    Provenance,
    ReviewStatus,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@dataclass
class StageResult:
    facts: list[Fact] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)
    escalations: list[tuple[str, str, EscalationRecord]] = field(default_factory=list)

    @property
    def confirmed_edges(self) -> list[KGEdge]:
        return [e for e in self.edges if e.status is ReviewStatus.CONFIRMED]

    @property
    def pending_edges(self) -> list[KGEdge]:
        return [e for e in self.edges if e.status is ReviewStatus.PENDING_REVIEW]


def _commit_unambiguous(candidate: CandidateEdge) -> KGEdge:
    return KGEdge(
        edge_id=candidate.edge_id,
        document_id=candidate.document_id,
        src_clause_ref=candidate.src_clause_ref,
        dst_clause_ref=candidate.dst_clause_ref,
        edge_type=candidate.candidate_types[0],
        status=ReviewStatus.CONFIRMED,
        confidence=candidate.confidence,
        evidence_chunk_ids=candidate.evidence_chunk_ids,
        pattern_key=candidate.pattern_key,
        resolved_by=Provenance.DIRECT_EXTRACTION,
    )


def run(chunks: list[Chunk], prior_edges: list[KGEdge] | None = None) -> StageResult:
    provider = get_provider()
    facts, candidates = clause_ner.extract(chunks, provider)

    ctx = ResolutionContext(
        chunks=chunks,
        provider=provider,
        confirmed_edges=list(prior_edges or []),
    )
    result = StageResult(facts=facts)

    # Document order matters: an edge confirmed early becomes precedent for a later
    # ambiguity via the kg_precedent strategy.
    for candidate in sorted(candidates, key=lambda c: c.edge_id):
        if not candidate.is_ambiguous:
            edge = _commit_unambiguous(candidate)
        else:
            resolution = resolve(candidate, ctx)
            if resolution.resolved:
                edge = confirm(candidate, resolution)
            else:
                edge, escalation_id, record = escalate(candidate, resolution)
                result.escalations.append((escalation_id, edge.edge_id, record))

        result.edges.append(edge)
        if edge.status is ReviewStatus.CONFIRMED:
            ctx.confirmed_edges.append(edge)

    return result


def _load(path: Path) -> tuple[dict, list[Chunk]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, [Chunk.model_validate(c) for c in payload["chunks"]]


def _dump(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    root = FIXTURES.parent
    print(f"wrote {path.relative_to(root) if path.is_relative_to(root) else path}")


def publish_fixtures(result: StageResult, out: Path = FIXTURES) -> None:
    """Regenerate the fixtures Dev 4 and Dev 1 build against.

    Generated from a real run rather than hand-written, so they cannot drift from what
    the stage actually emits.
    """
    out.mkdir(parents=True, exist_ok=True)
    _dump(out / "facts.sample.json", [f.model_dump(mode="json") for f in result.facts])
    _dump(
        out / "kg_edges.confirmed.json",
        [e.model_dump(mode="json") for e in result.confirmed_edges],
    )
    _dump(
        out / "kg_edges.pending_review.json",
        [e.model_dump(mode="json") for e in result.pending_edges],
    )
    _dump(
        out / "escalation.sample.json",
        [
            {"id": escalation_id, "target_edge_id": edge_id, **record.model_dump(mode="json")}
            for escalation_id, edge_id, record in result.escalations
        ],
    )


def _persist(payload: dict, result: StageResult) -> None:
    from app import models
    from app.auth.tags import normalize_tags
    from app.db import SessionLocal
    from app.kg import store

    with SessionLocal() as session:
        session.merge(
            models.Document(
                document_id=payload["document_id"],
                filename=payload["filename"],
                # Fixtures on disk predate revision 0004 and still carry the old object
                # shape (`{"confidentiality": "internal"}`). normalize_tags collapses it
                # to `["confidentiality:internal"]`, matching the migration's backfill.
                rbac_tags=normalize_tags(payload.get("rbac_tags")),
            )
        )
        for chunk in payload["chunks"]:
            session.merge(models.Chunk(**chunk))
        store.save_facts(session, result.facts)
        store.save_edges(session, result.edges)
        store.save_escalations(session, result.escalations)
        session.commit()


def _summarise(result: StageResult) -> None:
    print(f"facts:            {len(result.facts)}")
    print(f"confirmed edges:  {len(result.confirmed_edges)}")
    for edge in result.confirmed_edges:
        print(f"  {edge.src_clause_ref} -{edge.edge_type.value}-> {edge.dst_clause_ref}"
              f"  ({edge.resolved_by.value if edge.resolved_by else '?'}, conf {edge.confidence})")
    print(f"escalated:        {len(result.escalations)}")
    for _, edge_id, record in result.escalations:
        print(f"  {edge_id} from Section {record.clause_ref} after "
              f"{record.rounds_attempted} rounds")
        # The trace is the whole point of the queue item — print it, or the summary looks
        # identical to a stage that escalated without trying anything.
        for round_ in record.trace:
            print(f"      {round_.round}. {round_.attempt}: {round_.result}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Clause NER + Knowledge Graph over a document.")
    parser.add_argument("--chunks", type=Path, default=FIXTURES / "chunks.sample.json")
    parser.add_argument("--persist", action="store_true", help="write results to the Metadata DB")
    parser.add_argument("--publish", action="store_true", help="regenerate the published fixtures")
    parser.add_argument("--out", type=Path, help="write the output fixtures somewhere else")
    args = parser.parse_args()

    payload, chunks = _load(args.chunks)
    result = run(chunks)
    _summarise(result)

    if args.publish:
        # The published fixtures are a contract with Dev 1 and Dev 4 and are pinned by a
        # test. Publishing a different document over them would break both silently.
        if args.chunks.resolve() != (FIXTURES / "chunks.sample.json").resolve():
            parser.error("--publish only applies to fixtures/chunks.sample.json; use --out DIR")
        publish_fixtures(result)
    if args.out:
        publish_fixtures(result, args.out)
    if args.persist:
        _persist(payload, result)
        print("persisted to Metadata DB")


if __name__ == "__main__":
    main()
