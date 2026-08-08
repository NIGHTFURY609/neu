"""Tests for `app.risk.pipeline` — ported from `better-call-saul/test_gus.py`, plus the
FLAGGED/WAIVED-audit-record -> RiskFlag translation and an end-to-end run against the
real, published Stage 2 fixtures.
"""

import json
import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.risk.audit import EvaluationResult
from app.risk.pipeline import (
    NO_CLAUSE_SENTINEL,
    publish_audit_records,
    run_risk_assessment,
    to_risk_flags,
)
from app.risk.rules import Condition, Rule
from app.schemas import Fact, KGEdge

DOC = "DOC-001"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@dataclass(frozen=True)
class MockFact:
    fact_id: str
    document_id: str
    clause_ref: str
    fact_type: str
    value: dict


@dataclass(frozen=True)
class MockEdge:
    edge_id: str
    document_id: str
    src_clause_ref: str
    dst_clause_ref: str
    edge_type: str
    status: str


def _liability_rule():
    return Rule(
        rule_id="liability-cap-min", version=1, clause_pattern="liability_cap",
        conditions=(Condition(field="amount", operator="<", value=1_000_000),),
        severity="high", rationale="test", allowed_overrides=("OVERRIDES",),
    )


def _missing_clause_rule():
    return Rule(
        rule_id="has-liability-clause", version=1, clause_pattern="liability_cap",
        conditions=(Condition(field="amount", operator="is_missing"),),
        severity="critical", rationale="test",
    )


# --------------------------------------------------------------- run_risk_assessment


def test_compliant_when_fact_is_clean():
    # rule fires on amount < 1_000_000 (a cap that's too low), so 5M is compliant
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 5_000_000})
    records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
    assert len(records) == 1
    assert records[0].evaluation_result == EvaluationResult.COMPLIANT
    assert records[0].clause_ref == "2.2"


def test_flagged_when_under_cap_with_no_override():
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
    records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
    assert records[0].evaluation_result == EvaluationResult.FLAGGED


def test_waived_when_confirmed_override_exists():
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
    edge = MockEdge("E01", DOC, "4.1", "2.2", "OVERRIDES", "confirmed")
    records = run_risk_assessment(DOC, [_liability_rule()], [fact], [edge])
    assert records[0].evaluation_result == EvaluationResult.WAIVED


def test_system_error_on_bad_fact_value_does_not_crash_the_run():
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": "$1,000,000"})
    records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
    assert records[0].evaluation_result == EvaluationResult.SYSTEM_ERROR


def test_wholly_absent_fact_type_still_evaluates_is_missing_rule():
    # no facts at all -> the is_missing rule must still get a chance to fire
    records = run_risk_assessment(DOC, [_missing_clause_rule()], [], [])
    assert len(records) == 1
    assert records[0].evaluation_result == EvaluationResult.FLAGGED
    assert records[0].clause_ref == NO_CLAUSE_SENTINEL


def test_present_fact_type_means_is_missing_rule_does_not_fire():
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
    records = run_risk_assessment(DOC, [_missing_clause_rule()], [fact], [])
    assert records[0].evaluation_result == EvaluationResult.COMPLIANT


def test_fact_from_wrong_document_raises():
    fact = MockFact("F01", "DOC-999", "2.2", "liability_cap", {"amount": 500_000})
    with pytest.raises(ValueError):
        run_risk_assessment(DOC, [_liability_rule()], [fact], [])


def test_malformed_unrelated_fact_does_not_crash_whole_assessment():
    # a fact with an empty clause_ref, of a type no rule even cares about, must not
    # take down evaluation of the other, perfectly good facts in the same document
    good = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
    bad = MockFact("F02", DOC, "", "obligation", {"x": 1})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        records = run_risk_assessment(DOC, [_liability_rule()], [good, bad], [])
    assert len(records) == 1
    assert records[0].evaluation_result == EvaluationResult.FLAGGED
    assert any("malformed fact row" in str(w.message) for w in caught)


def test_malformed_fact_of_relevant_type_becomes_system_error_not_false_absence():
    # Extraction WAS attempted for liability_cap (the row exists), it's just corrupt.
    # Must be SYSTEM_ERROR, not a false FLAGGED "no clause at all".
    bad = MockFact("F01", DOC, "", "liability_cap", {"amount": 5_000_000})
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        records = run_risk_assessment(DOC, [_missing_clause_rule()], [bad], [])
    assert len(records) == 1
    assert records[0].evaluation_result == EvaluationResult.SYSTEM_ERROR
    assert "liability_cap" in records[0].system_error


def test_whitespace_padded_field_rejected():
    fact = MockFact("F01", DOC, " 2.2 ", "liability_cap", {"amount": 500_000})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
    # clause_ref fails validation but fact_type is still readable, so this is
    # attempted-but-failed (SYSTEM_ERROR), not treated as wholly absent
    assert records[0].evaluation_result == EvaluationResult.SYSTEM_ERROR
    assert any("surrounding whitespace" in str(w.message) for w in caught)


def test_rejects_non_rule_in_rules():
    with pytest.raises(TypeError):
        run_risk_assessment(DOC, ["not-a-rule"], [], [])


def test_rejects_empty_document_id():
    with pytest.raises(TypeError):
        run_risk_assessment("", [_liability_rule()], [], [])


def test_multiple_occurrences_of_same_fact_type_each_evaluated():
    rule = Rule(
        rule_id="obligation-deadline", version=1, clause_pattern="obligation",
        conditions=(Condition(field="deadline_days", operator="<=", value=30),),
        severity="medium", rationale="test",
    )
    # rule fires on deadline_days <= 30 (too tight a deadline)
    f1 = MockFact("F01", DOC, "2.4", "obligation", {"deadline_days": 30})
    f2 = MockFact("F02", DOC, "2.6", "obligation", {"deadline_days": 60})
    records = run_risk_assessment(DOC, [rule], [f1, f2], [])
    assert len(records) == 2
    results = {r.clause_ref: r.evaluation_result for r in records}
    assert results["2.4"] == EvaluationResult.FLAGGED
    assert results["2.6"] == EvaluationResult.COMPLIANT


def test_publish_audit_records_includes_everything(tmp_path):
    compliant = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 5_000_000})
    records = run_risk_assessment(DOC, [_liability_rule()], [compliant], [])
    out = tmp_path / "audit.json"
    publish_audit_records(records, out)
    data = json.loads(out.read_text())
    assert len(data) == 1
    assert data[0]["evaluation_result"] == "COMPLIANT"


# --------------------------------------------------------------------- to_risk_flags


def test_to_risk_flags_only_includes_flagged_and_waived():
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 5_000_000})  # compliant
    records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
    flags = to_risk_flags(DOC, records, [_liability_rule()], [fact])
    assert flags == []


def test_to_risk_flags_maps_flagged_and_carries_triggering_fact_id():
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
    records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
    flags = to_risk_flags(DOC, records, [_liability_rule()], [fact])
    assert len(flags) == 1
    flag = flags[0]
    assert flag.status.value == "flagged"
    assert flag.severity.value == "high"
    assert flag.triggering_fact_ids == ["F01"]
    assert flag.suppressing_edge_id is None
    assert flag.id.startswith("RISK-DOC-001-")


def test_to_risk_flags_waived_carries_suppressing_edge_and_rule_severity():
    # audit.py sets final_severity=None for WAIVED — the rule's own severity must be
    # used instead, matching fixtures/risk.sample.json's suppressed entries.
    fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
    edge = MockEdge("E01", DOC, "4.1", "2.2", "OVERRIDES", "confirmed")
    records = run_risk_assessment(DOC, [_liability_rule()], [fact], [edge])
    flags = to_risk_flags(DOC, records, [_liability_rule()], [fact])
    assert len(flags) == 1
    flag = flags[0]
    assert flag.status.value == "suppressed"
    assert flag.severity.value == "high"
    assert flag.suppressing_edge_id == "E01"


# --------------------------------------------------------- real Stage 2 fixture, end-to-end


def test_runs_end_to_end_against_real_published_fixtures():
    """Sanity check against Stage 2's actual published output, not just mocks."""
    facts = [Fact.model_validate(f) for f in json.loads((FIXTURES / "facts.sample.json").read_text())]
    edges = [KGEdge.model_validate(e) for e in json.loads((FIXTURES / "kg_edges.confirmed.json").read_text())]

    records = run_risk_assessment("DOC-001", [_liability_rule()], facts, edges)

    assert len(records) == 1  # one liability_cap fact in the real fixture
    record = records[0]
    assert record.clause_ref == "2.2"
    # Stage 2 used to publish value.amount as the matched text ("$1,000,000"), which
    # `evaluator._numeric` rightly refused to coerce — so every liability rule died as
    # SYSTEM_ERROR and `to_risk_flags` then dropped the record, producing no flag and no
    # error. `extraction.clause_ner._coerce_money` now normalizes at the boundary, so the
    # rule actually evaluates. The evaluator's strictness is unchanged and still pinned by
    # `test_system_error_on_bad_fact_value_does_not_crash_the_run` above.
    assert record.evaluation_result == EvaluationResult.COMPLIANT
    assert record.system_error is None

    fact = next(f for f in facts if f.fact_type.value == "liability_cap")
    assert fact.value["amount"] == 1_000_000  # numeric, comparable
    assert fact.value["amount_text"] == "$1,000,000"  # original preserved for the reviewer
