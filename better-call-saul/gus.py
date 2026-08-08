


"""Function 5 — Data I/O & orchestration.

Reads Dev 3's facts + confirmed KG edges and this stage's own playbook, runs every rule
against every clause it applies to through functions 2 -> 3 -> 4, and publishes the
risk-flagged-clause fixture the Redline Generator needs (WORK-SPLIT.md).

Still works entirely off JSON fixtures, not a live DB connection — same "mock now,
connect later" approach as every other function here.

Evaluation unit: a rule with clause_pattern="liability_cap" runs once per Dev 3 Fact row
of that fact_type (each row already bundles one clause instance's fields, e.g. amount +
currency together). If a fact type is wholly absent from the document, the rule still
runs once against an empty facts dict, with clause_ref set to NO_CLAUSE_SENTINEL — that's
the only way an is_missing rule ("no liability cap clause at all") can ever fire, since
there's no row to iterate for a type that isn't there. This interpretation isn't
team-confirmed, just the one that makes is_missing rules actually reachable.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, Sequence, runtime_checkable

from chuck import AuditRecord, EvaluationResult, generate_audit_record
from kim import Rule
from lalo import DataTypeError, FactValue, evaluate_rule
from mike import KGEdgeLike, find_confirmed_overrides

NO_CLAUSE_SENTINEL = "(document-level, no matching clause)"


@runtime_checkable
class FactRowLike(Protocol):
    fact_id: str
    document_id: str
    clause_ref: str
    fact_type: str
    value: dict[str, Any]
    confidence: float


def _validate_fact_row(row: object, document_id: str) -> FactRowLike:
    if not isinstance(row, FactRowLike):
        raise TypeError(
            f"Expected a fact row with fact_id/document_id/clause_ref/fact_type/value/confidence, got {type(row).__name__}"
        )
    # Document scoping is checked FIRST, before any content-quality check — != never
    # raises regardless of row.document_id's type, so this is always safe here. Ordering
    # matters: group_facts_by_clause_pattern only skips-and-warns on TypeError, never on
    # this ValueError, specifically so a wrong-document row can't slip through silently
    # just because it also happens to have some other content problem.
    if row.document_id != document_id:
        raise ValueError(
            f"fact {row.fact_id!r} belongs to document {row.document_id!r}, not {document_id!r} — "
            f"caller must scope facts to the document being evaluated"
        )
    for attr in ("fact_id", "document_id", "clause_ref", "fact_type"):
        value = getattr(row, attr)
        # Reject surrounding whitespace outright rather than silently trusting a padded
        # value — same policy as kim.py's _clean_str, applied consistently here too.
        if not isinstance(value, str) or not value or value != value.strip():
            raise TypeError(f"Fact row {attr!r} must be a non-empty string with no surrounding whitespace, got {value!r}")
    if not isinstance(row.value, dict):
        raise TypeError(f"Fact row 'value' must be a dict, got {type(row.value).__name__}")
    if isinstance(row.confidence, bool) or not isinstance(row.confidence, (int, float)):
        raise TypeError(f"Fact row 'confidence' must be numeric, got {type(row.confidence).__name__}")
    if not (0.0 <= row.confidence <= 1.0):
        raise TypeError(f"Fact row 'confidence' must be within [0, 1], got {row.confidence!r}")
    return row


def group_facts_by_clause_pattern(
    facts: Sequence[FactRowLike], document_id: str
) -> tuple[dict[str, list[FactRowLike]], set[str]]:
    """fact_type is Dev 3's name for what kim.py calls clause_pattern — same field, different name.

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
    mike.py treats a document_id mismatch as a hard failure rather than something to
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
            raise TypeError(f"Expected a kim.Rule in rules, got {type(rule).__name__}")

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
            # Confidence describes the INPUT (how sure Dev 3 was about this extraction),
            # so it's attached whenever a row exists, regardless of outcome. fact_id is
            # only "triggering" once we know the rule actually fired — see below.
            confidence = row.confidence if row is not None else None

            try:
                fired = evaluate_rule(fact_dict, rule)
            except DataTypeError as e:
                records.append(
                    generate_audit_record(
                        document_id, clause_ref, rule, fact_dict, rule_fired=False, error_message=str(e),
                        extraction_confidence=confidence,
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
                            document_id, clause_ref, rule, fact_dict, rule_fired=fired, error_message=str(e),
                            extraction_confidence=confidence,
                        )
                    )
                    continue

            triggering_fact_ids = (row.fact_id,) if (row is not None and fired) else ()
            records.append(
                generate_audit_record(
                    document_id, clause_ref, rule, fact_dict, rule_fired=fired, overrides=overrides,
                    extraction_confidence=confidence, triggering_fact_ids=triggering_fact_ids,
                )
            )

    return records


def _load_namespace_list(path: str | Path) -> list[SimpleNamespace]:
    """Wraps raw JSON dicts as attribute-accessible objects — matches how Dev 3's real
    Pydantic models behave, so this drops straight in once we connect for real."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError(f"{path}: JSON root must be a list, got {type(raw).__name__}")
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(f"{path}: item at index {i} must be a JSON object, got {type(entry).__name__}")
    return [SimpleNamespace(**entry) for entry in raw]


def load_facts_fixture(path: str | Path) -> list[SimpleNamespace]:
    return _load_namespace_list(path)


def load_kg_edges_fixture(path: str | Path) -> list[SimpleNamespace]:
    return _load_namespace_list(path)


def publish_audit_records(records: Sequence[AuditRecord], out_path: str | Path) -> None:
    """The full audit trail — every outcome, not just flags. Dev 1's dashboard territory."""
    Path(out_path).write_text(
        json.dumps([r.to_json_dict() for r in records], indent=2) + "\n", encoding="utf-8"
    )


def publish_flagged_fixture(records: Sequence[AuditRecord], out_path: str | Path) -> None:
    """The WORK-SPLIT.md deliverable — only FLAGGED clauses are what triggers a redline."""
    flagged = [r.to_json_dict() for r in records if r.evaluation_result is EvaluationResult.FLAGGED]
    Path(out_path).write_text(json.dumps(flagged, indent=2) + "\n", encoding="utf-8")
