"""Run the Redline Generator over a document's risk flags.

    risk flags + playbook -> [active retrieval] -> redlines | escalations

Same shape as `app.pipeline`: runnable with or without a database, `--persist` writes to
the Metadata DB, and without it the stage runs purely in memory, which is how the
published fixtures are regenerated.

Inputs are everything upstream produced. Confirmed and pending edges are loaded from
*separate* files and used differently on purpose — the confirmed ones are retrievable,
the pending ones only tell the loop which clause pairs the Knowledge Graph has not
settled (§3.3). If they arrived as one list, "treat pending as absent" would be a thing
this file had to remember rather than a thing the types enforce.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.redline.generator import GenerationRun, run
from app.redline.provider import get_provider
from app.redline.retrieval import RetrievalContext
from app.schemas import Chunk, Fact, KGEdge, PlaybookRule, RiskFlag

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_context(
    chunks: list[Chunk],
    confirmed_edges: list[KGEdge],
    facts: list[Fact],
    pending_edges: list[KGEdge],
) -> RetrievalContext:
    return RetrievalContext(
        chunks=chunks,
        confirmed_edges=confirmed_edges,
        facts=facts,
        # Refs only. The loop learns *that* the pair is unsettled, never what the
        # unconfirmed edge claimed.
        unresolved_pairs=[(e.src_clause_ref, e.dst_clause_ref) for e in pending_edges],
    )


def _dump(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    root = FIXTURES.parent
    print(f"wrote {path.relative_to(root) if path.is_relative_to(root) else path}")


def publish_fixtures(result: GenerationRun, out: Path = FIXTURES) -> None:
    """Regenerate the fixtures Dev 1 renders.

    `redline.sample.json` was hand-written while this stage did not exist. It is
    generated now, so it cannot drift from what the stage actually emits.
    """
    out.mkdir(parents=True, exist_ok=True)
    _dump(out / "redline.sample.json", [r.model_dump(mode="json") for r in result.redlines])
    _dump(
        out / "escalation.redline.json",
        [
            {"id": escalation_id, "target_redline_id": redline_id, **record.model_dump(mode="json")}
            for escalation_id, redline_id, record in result.escalations
        ],
    )


def _persist(result: GenerationRun) -> None:
    from app.db import SessionLocal
    from app.redline import store

    with SessionLocal() as session:
        # Redlines first: the escalation's target_redline_id is a foreign key into them.
        store.save_redlines(session, result.redlines)
        store.save_redline_escalations(session, result.escalations)
        session.commit()


def _summarise(result: GenerationRun) -> None:
    print(f"redlines:         {len(result.redlines)} "
          f"({len(result.confirmed)} confirmed, "
          f"{len(result.redlines) - len(result.confirmed)} held for review)")
    for redline in result.redlines:
        print(f"  {redline.redline_id}  Section {redline.clause_ref}  "
              f"{redline.status.value}  conf {redline.confidence}  "
              f"{redline.rounds_attempted} round(s)")
        for round_ in redline.trace:
            print(f"      {round_.round}. {round_.attempt}: {round_.result}")

    print(f"escalated:        {len(result.escalations)}")
    for escalation_id, redline_id, record in result.escalations:
        target = redline_id or "no redline written"
        print(f"  {escalation_id}  Section {record.clause_ref}  "
              f"{record.reason.value}  after {record.rounds_attempted} round(s)  [{target}]")
        # The trace is the queue item. Without it the summary cannot tell a stage that
        # gave up immediately from one that exhausted the document first.
        for round_ in record.trace:
            print(f"      {round_.round}. {round_.attempt}: {round_.result}")

    print(f"suppressed flags: {len(result.suppressed)}")
    for risk in result.suppressed:
        print(f"  {risk.id}  Section {risk.clause_ref}  waived by "
              f"{risk.suppressing_edge_id}, no redline")

    print(f"rule not found:   {len(result.rule_not_found)}")
    for risk in result.rule_not_found:
        print(f"  {risk.id}  Section {risk.clause_ref}  playbook has no "
              f"{risk.rule_id} v{risk.rule_version}, skipped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Redline Generator over a document's risk flags."
    )
    parser.add_argument("--risks", type=Path, default=FIXTURES / "risk.sample.json")
    parser.add_argument("--playbook", type=Path, default=FIXTURES / "playbook.sample.json")
    parser.add_argument("--chunks", type=Path, default=FIXTURES / "chunks.sample.json")
    parser.add_argument("--edges", type=Path, default=FIXTURES / "kg_edges.confirmed.json")
    parser.add_argument("--pending", type=Path, default=FIXTURES / "kg_edges.pending_review.json")
    parser.add_argument("--facts", type=Path, default=FIXTURES / "facts.sample.json")
    parser.add_argument("--persist", action="store_true", help="write results to the Metadata DB")
    parser.add_argument("--publish", action="store_true", help="regenerate the published fixtures")
    parser.add_argument("--out", type=Path, help="write the output fixtures somewhere else")
    args = parser.parse_args()

    chunks = [Chunk.model_validate(c) for c in _read(args.chunks)["chunks"]]
    ctx = build_context(
        chunks=chunks,
        confirmed_edges=[KGEdge.model_validate(e) for e in _read(args.edges)],
        facts=[Fact.model_validate(f) for f in _read(args.facts)],
        pending_edges=[KGEdge.model_validate(e) for e in _read(args.pending)],
    )
    risks = [RiskFlag.model_validate(r) for r in _read(args.risks)]
    playbook = [PlaybookRule.model_validate(r) for r in _read(args.playbook)]

    result = run(risks, playbook, ctx, get_provider())
    _summarise(result)

    if args.publish:
        # Same guard as app.pipeline: the published fixtures are a contract with Dev 1,
        # and publishing a different document over them would break it silently.
        if args.risks.resolve() != (FIXTURES / "risk.sample.json").resolve():
            parser.error("--publish only applies to fixtures/risk.sample.json; use --out DIR")
        publish_fixtures(result)
    if args.out:
        publish_fixtures(result, args.out)
    if args.persist:
        _persist(result)
        print("persisted to Metadata DB")


if __name__ == "__main__":
    main()
