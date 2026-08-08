"""Run the Risk & Rules Engine over a document — Dev 4's `gus.py`, ported and wired to
the real pipeline.

    facts + confirmed KG edges + playbook -> [rule x clause] -> audit records -> risk flags

Same shape as `app.pipeline` and `app.redline.pipeline`: everything is read from JSON
fixtures by default (facts/edges are Stage 2's real published output; the playbook is
this stage's own input), `--persist` additionally writes to the Metadata DB, and without
it the stage runs purely in memory — which is how `fixtures/risk.sample.json` itself is
regenerated (`--publish`), replacing the hand-written version.

Evaluation unit: a rule with clause_pattern="liability_cap" runs once per Fact row of
that fact_type (each row already bundles one clause instance's fields, e.g. amount +
currency together). If a fact type is wholly absent from the document, the rule still
runs once against an empty facts dict, with clause_ref set to NO_CLAUSE_SENTINEL — that's
the only way an is_missing rule ("no liability cap clause at all") can ever fire, since
there's no row to iterate for a type that isn't there.
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from app.risk.audit import AuditRecord, EvaluationResult, generate_audit_record
from app.risk.evaluator import DataTypeError, FactValue, evaluate_rule
from app.risk.overrides import KGEdgeLike, find_confirmed_overrides
from app.risk.rules import Rule, load_playbook
from app.schemas import Fact, KGEdge, RiskFlag, RiskFlagStatus

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

NO_CLAUSE_SENTINEL = "(document-level, no matching clause)"


@runtime_checkable
class FactRowLike(Protocol):
    fact_id: str
    document_id: str
    clause_ref: str
    fact_type: str
    value: dict[str, Any]


def _validate_fact_row(row: object, document_id: str) -> FactRowLike:
    if not isinstance(row, FactRowLike):
        raise TypeError(
            f"Expected a fact row with fact_id/document_id/clause_ref/fact_type/value, got {type(row).__name__}"
        )
    for attr in ("fact_id", "document_id", "clause_ref", "fact_type"):
        value = getattr(row, attr)
        # Reject surrounding whitespace outright rather than silently trusting a padded
        # value — same policy as rules.py's _clean_str, applied consistently here too.
        if not isinstance(value, str) or not value or value != value.strip():
            raise TypeError(f"Fact row {attr!r} must be a non-empty string with no surrounding whitespace, got {value!r}")
    if not isinstance(row.value, dict):
        raise TypeError(f"Fact row 'value' must be a dict, got {type(row.value).__name__}")
    if row.document_id != document_id:
        raise ValueError(
            f"fact {row.fact_id!r} belongs to document {row.document_id!r}, not {document_id!r} — "
            f"caller must scope facts to the document being evaluated"
        )
    return row


def group_facts_by_clause_pattern(
    facts: Sequence[FactRowLike], document_id: str
) -> tuple[dict[str, list[FactRowLike]], set[str]]:
    """`fact_type` is Stage 2's name for what rules.py calls `clause_pattern` — same field,
    different name.

    Returns (grouped, invalid_fact_types). invalid_fact_types names every fact_type that
    had at least one row fail validation — this is what lets run_risk_assessment tell "this
    fact type never existed" (fine, an is_missing rule should fire) apart from "this fact
    type existed but its data was unusable" (not fine — that must become a SYSTEM_ERROR,
    not silently look identical to genuine absence). A row whose own fact_type couldn't be
    read at all can't be attributed to any specific clause_pattern; it's still warned
    about, just not trackable here.

    A row with a shape/content problem (TypeError) is skipped rather than aborting every
    other clause's assessment — the same blast-radius containment run_risk_assessment
    applies to evaluation errors, just one stage earlier. Not silently dropped either:
    skipping still warns, and (when attributable) still surfaces as a SYSTEM_ERROR record
    for whichever rule(s) depend on that fact type.

    A row from the wrong document (ValueError) is NOT skipped — that indicates the
    caller's own scoping is broken, not one row's data quality, and mirrors how
    overrides.py treats a document_id mismatch as a hard failure rather than something to
    tolerate silently.
    """
    grouped: dict[str, list[FactRowLike]] = {}
    invalid_fact_types: set[str] = set()
    for raw in facts:
        try:
            row = _validate_fact_row(raw, document_id)
        except TypeError as e:
            fact_type = getattr(raw, "fact_type", None)
            if isinstance(fact_type, str) and fact_type.strip():
                invalid_fact_types.add(fact_type)
            warnings.warn(f"Skipping malformed fact row: {e}", stacklevel=2)
            continue
        grouped.setdefault(row.fact_type, []).append(row)
    return grouped, invalid_fact_types


def run_risk_assessment(
    document_id: str,
    rules: Sequence[Rule],
    facts: Sequence[FactRowLike],
    kg_edges: Sequence[KGEdgeLike],
) -> list[AuditRecord]:
    """Evaluate every rule against every clause it applies to. Never lets a bad fact or a
    bad KG edge crash the whole run — each failure becomes one SYSTEM_ERROR record for
    that specific (clause, rule) pair, and evaluation continues for everything else."""
    if not isinstance(document_id, str) or not document_id.strip():
        raise TypeError(f"document_id must be a non-empty string, got {document_id!r}")
    for rule in rules:
        if not isinstance(rule, Rule):
            raise TypeError(f"Expected a risk.rules.Rule in rules, got {type(rule).__name__}")

    grouped, invalid_fact_types = group_facts_by_clause_pattern(facts, document_id)
    records: list[AuditRecord] = []

    for rule in rules:
        matching_rows = grouped.get(rule.clause_pattern, [])

        if not matching_rows and rule.clause_pattern in invalid_fact_types:
            # Extraction was attempted for this clause type but every row was unusable —
            # NOT the same as "this fact type doesn't exist," so it must not fall through
            # to the wholly-absent path below and risk an is_missing rule firing (or not
            # firing) on the strength of data we know is corrupt, not confirmed absent.
            records.append(
                generate_audit_record(
                    document_id, NO_CLAUSE_SENTINEL, rule, {}, rule_fired=False,
                    error_message=f"All extracted {rule.clause_pattern!r} facts failed validation for this document.",
                )
            )
            continue

        occurrences: Sequence[FactRowLike | None] = matching_rows if matching_rows else [None]

        for row in occurrences:
            clause_ref = row.clause_ref if row is not None else NO_CLAUSE_SENTINEL
            fact_dict: dict[str, FactValue] = dict(row.value) if row is not None else {}

            try:
                fired = evaluate_rule(fact_dict, rule)
            except DataTypeError as e:
                records.append(
                    generate_audit_record(
                        document_id, clause_ref, rule, fact_dict, rule_fired=False, error_message=str(e)
                    )
                )
                continue

            overrides: tuple[KGEdgeLike, ...] = ()
            if fired:
                try:
                    overrides = find_confirmed_overrides(document_id, clause_ref, rule, kg_edges)
                except (TypeError, ValueError) as e:
                    records.append(
                        generate_audit_record(
                            document_id, clause_ref, rule, fact_dict, rule_fired=fired, error_message=str(e)
                        )
                    )
                    continue

            records.append(
                generate_audit_record(document_id, clause_ref, rule, fact_dict, rule_fired=fired, overrides=overrides)
            )

    return records


def publish_audit_records(records: Sequence[AuditRecord], out_path: str | Path) -> None:
    """The full audit trail — every outcome, not just flags."""
    Path(out_path).write_text(
        json.dumps([r.to_json_dict() for r in records], indent=2) + "\n", encoding="utf-8"
    )


def to_risk_flags(
    document_id: str,
    records: Sequence[AuditRecord],
    rules: Sequence[Rule],
    facts: Sequence[FactRowLike],
) -> list[RiskFlag]:
    """Maps FLAGGED/WAIVED audit records onto the `risk_flags` shape the Redline
    Generator and dashboard consume (`app.schemas.RiskFlag`).

    COMPLIANT is a non-event, not a flag. SYSTEM_ERROR has no risk_flags row to become —
    `risk_flags.status` only has room for 'flagged'/'suppressed' (chuck.py's own
    docstring calls this a known, still-open gap); callers should surface SYSTEM_ERROR
    records separately (`_summarise` below prints them).

    `severity` always comes from the rule (`rules`), not `record.final_severity` —
    audit.py only fills `final_severity` for FLAGGED, but a suppressed flag still needs
    to say how severe it *would have been* (fixtures/risk.sample.json's own suppressed
    entry carries a severity for exactly this reason).

    `triggering_fact_ids` is reconstructed from `facts` by (clause_ref, fact_type)
    rather than carried on AuditRecord itself — audit.py's `fact_evaluated` only holds
    the values dict, not the originating fact_id, so this stays a pipeline-layer concern
    instead of changing the audit record's own tested shape.
    """
    rule_index = {(r.rule_id, r.version): r for r in rules}
    fact_ids_by_clause: dict[tuple[str, str], list[str]] = {}
    for f in facts:
        fact_ids_by_clause.setdefault((f.clause_ref, f.fact_type), []).append(f.fact_id)

    flags: list[RiskFlag] = []
    for record in records:
        if record.evaluation_result not in (EvaluationResult.FLAGGED, EvaluationResult.WAIVED):
            continue
        rule = rule_index.get((record.rule_triggered, record.rule_version))
        if rule is None:
            continue  # shouldn't happen — the record was produced from this same rule set
        flags.append(
            RiskFlag(
                id=f"RISK-{document_id}-{len(flags) + 1:03d}",
                document_id=document_id,
                clause_ref=record.clause_ref,
                rule_id=record.rule_triggered,
                rule_version=record.rule_version,
                severity=rule.severity,
                status=(
                    RiskFlagStatus.FLAGGED
                    if record.evaluation_result is EvaluationResult.FLAGGED
                    else RiskFlagStatus.SUPPRESSED
                ),
                suppressing_edge_id=(
                    record.kg_overrides_found[0]["edge_id"] if record.kg_overrides_found else None
                ),
                triggering_fact_ids=fact_ids_by_clause.get((record.clause_ref, rule.clause_pattern), []),
            )
        )
    return flags


# --------------------------------------------------------------------------------- CLI


def _read(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _infer_document_id(facts: Sequence[Fact]) -> str:
    ids = {f.document_id for f in facts}
    if len(ids) != 1:
        raise SystemExit(f"facts span {len(ids)} documents {sorted(ids)}; pass --document-id explicitly")
    return ids.pop()


def _summarise(document_id: str, records: Sequence[AuditRecord], flags: Sequence[RiskFlag]) -> None:
    by_result: dict[EvaluationResult, int] = {}
    for r in records:
        by_result[r.evaluation_result] = by_result.get(r.evaluation_result, 0) + 1
    print(f"document:   {document_id}")
    print(f"evaluated:  {len(records)} (rule, clause) pairs")
    for result in EvaluationResult:
        print(f"  {result.value:<12} {by_result.get(result, 0)}")
    for flag in flags:
        print(f"  {flag.status.value:<10} {flag.clause_ref}  {flag.rule_id} v{flag.rule_version}  ({flag.severity.value})")
    for r in records:
        if r.evaluation_result is EvaluationResult.SYSTEM_ERROR:
            print(f"  ERROR      {r.clause_ref}  {r.rule_triggered} v{r.rule_version}  {r.system_error}")


def _persist(flags: Sequence[RiskFlag]) -> None:
    from app.db import SessionLocal
    from app.risk import store

    with SessionLocal() as session:
        store.save_risk_flags(session, flags)
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Risk & Rules Engine over a document.")
    parser.add_argument("--facts", type=Path, default=FIXTURES / "facts.sample.json")
    parser.add_argument("--edges", type=Path, default=FIXTURES / "kg_edges.confirmed.json")
    parser.add_argument("--playbook", type=Path, default=FIXTURES / "playbook.sample.json")
    parser.add_argument("--document-id", help="Defaults to the single document_id shared by --facts.")
    parser.add_argument("--persist", action="store_true", help="write flagged/waived results to the Metadata DB")
    parser.add_argument("--publish", action="store_true", help="regenerate the published risk.sample.json fixture")
    parser.add_argument("--out", type=Path, help="write the output fixture somewhere else")
    args = parser.parse_args()

    facts = [Fact.model_validate(f) for f in _read(args.facts)]
    kg_edges = [KGEdge.model_validate(e) for e in _read(args.edges)]
    rules = load_playbook(args.playbook)
    document_id = args.document_id or _infer_document_id(facts)

    records = run_risk_assessment(document_id, rules, facts, kg_edges)
    flags = to_risk_flags(document_id, records, rules, facts)
    _summarise(document_id, records, flags)

    if args.publish:
        # The published fixture is a contract with the Redline Generator and the
        # dashboard, and is pinned by a test — publishing a different document over it
        # would break both silently.
        if args.facts.resolve() != (FIXTURES / "facts.sample.json").resolve():
            parser.error("--publish only applies to fixtures/facts.sample.json; use --out instead")
        out = FIXTURES / "risk.sample.json"
        out.write_text(json.dumps([f.model_dump(mode="json") for f in flags], indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(FIXTURES.parent)}")
    if args.out:
        args.out.write_text(json.dumps([f.model_dump(mode="json") for f in flags], indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    if args.persist:
        _persist(flags)
        print("persisted to Metadata DB")


if __name__ == "__main__":
    main()
