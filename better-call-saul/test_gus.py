"""Tests for gus.py — mock data first, then a real end-to-end run against Dev 3's fixtures."""

import json
import tempfile
import unittest
import warnings
from dataclasses import dataclass
from pathlib import Path

from chuck import EvaluationResult
from gus import (
    NO_CLAUSE_SENTINEL,
    load_facts_fixture,
    load_kg_edges_fixture,
    publish_audit_records,
    publish_flagged_fixture,
    run_risk_assessment,
)
from kim import Condition, Rule

DOC = "DOC-001"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@dataclass(frozen=True)
class MockFact:
    fact_id: str
    document_id: str
    clause_ref: str
    fact_type: str
    value: dict
    confidence: float = 0.9


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


class RunRiskAssessmentTests(unittest.TestCase):
    def test_compliant_when_fact_is_clean(self):
        # rule fires on amount < 1_000_000 (a cap that's too low), so 5M is compliant
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 5_000_000})
        records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].evaluation_result, EvaluationResult.COMPLIANT)
        self.assertEqual(records[0].clause_ref, "2.2")

    def test_flagged_when_under_cap_with_no_override(self):
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
        records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
        self.assertEqual(records[0].evaluation_result, EvaluationResult.FLAGGED)

    def test_confidence_and_fact_id_propagate_to_the_audit_record(self):
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000}, confidence=0.87)
        records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
        self.assertEqual(records[0].extraction_confidence, 0.87)
        self.assertEqual(records[0].triggering_fact_ids, ("F01",))

    def test_confidence_present_but_triggering_fact_ids_empty_when_compliant(self):
        # confidence describes the input regardless of outcome; triggering_fact_ids only
        # makes sense once something was actually triggered
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 5_000_000}, confidence=0.87)
        records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
        self.assertEqual(records[0].evaluation_result, EvaluationResult.COMPLIANT)
        self.assertEqual(records[0].extraction_confidence, 0.87)
        self.assertEqual(records[0].triggering_fact_ids, ())

    def test_rejects_out_of_range_confidence(self):
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000}, confidence=1.5)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
        self.assertEqual(records[0].evaluation_result, EvaluationResult.SYSTEM_ERROR)
        self.assertTrue(any("confidence" in str(w.message) for w in caught))

    def test_waived_when_confirmed_override_exists(self):
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
        edge = MockEdge("E01", DOC, "4.1", "2.2", "OVERRIDES", "confirmed")
        records = run_risk_assessment(DOC, [_liability_rule()], [fact], [edge])
        self.assertEqual(records[0].evaluation_result, EvaluationResult.WAIVED)

    def test_system_error_on_bad_fact_value_does_not_crash_the_run(self):
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": "$1,000,000"})
        records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
        self.assertEqual(records[0].evaluation_result, EvaluationResult.SYSTEM_ERROR)

    def test_wholly_absent_fact_type_still_evaluates_is_missing_rule(self):
        # no facts at all -> the is_missing rule must still get a chance to fire
        records = run_risk_assessment(DOC, [_missing_clause_rule()], [], [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].evaluation_result, EvaluationResult.FLAGGED)
        self.assertEqual(records[0].clause_ref, NO_CLAUSE_SENTINEL)

    def test_present_fact_type_means_is_missing_rule_does_not_fire(self):
        fact = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
        records = run_risk_assessment(DOC, [_missing_clause_rule()], [fact], [])
        self.assertEqual(records[0].evaluation_result, EvaluationResult.COMPLIANT)

    def test_fact_from_wrong_document_raises(self):
        fact = MockFact("F01", "DOC-999", "2.2", "liability_cap", {"amount": 500_000})
        with self.assertRaises(ValueError):
            run_risk_assessment(DOC, [_liability_rule()], [fact], [])

    def test_wrong_document_fact_raises_even_with_also_bad_confidence(self):
        # Regression: document scoping must be checked before content-quality checks, so
        # a row that's both wrong-document AND has bad confidence still hard-fails as a
        # scoping bug, instead of being silently skipped as "just" malformed content.
        fact = MockFact("F01", "DOC-999", "2.2", "liability_cap", {"amount": 500_000}, confidence=1.5)
        with self.assertRaises(ValueError):
            run_risk_assessment(DOC, [_liability_rule()], [fact], [])

    def test_malformed_unrelated_fact_does_not_crash_whole_assessment(self):
        # a fact with an empty clause_ref, of a type no rule even cares about, must not
        # take down evaluation of the other, perfectly good facts in the same document
        good = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 500_000})
        bad = MockFact("F02", DOC, "", "obligation", {"x": 1})
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = run_risk_assessment(DOC, [_liability_rule()], [good, bad], [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].evaluation_result, EvaluationResult.FLAGGED)
        self.assertTrue(any("malformed fact row" in str(w.message) for w in caught))

    def test_malformed_fact_of_relevant_type_becomes_system_error_not_false_absence(self):
        # Reproduces the actual bug: extraction WAS attempted for liability_cap (the row
        # exists), it's just corrupt. Before the fix this fell through to the wholly-absent
        # path and produced a false FLAGGED "no clause at all" — indistinguishable from a
        # genuine missing clause. It must be SYSTEM_ERROR instead.
        bad = MockFact("F01", DOC, "", "liability_cap", {"amount": 5_000_000})
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            records = run_risk_assessment(DOC, [_missing_clause_rule()], [bad], [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].evaluation_result, EvaluationResult.SYSTEM_ERROR)
        self.assertIn("liability_cap", records[0].system_error)

    def test_whitespace_padded_field_rejected(self):
        fact = MockFact("F01", DOC, " 2.2 ", "liability_cap", {"amount": 500_000})
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            records = run_risk_assessment(DOC, [_liability_rule()], [fact], [])
        # clause_ref fails validation but fact_type is still readable, so this is
        # attempted-but-failed (SYSTEM_ERROR), not treated as wholly absent
        self.assertEqual(records[0].evaluation_result, EvaluationResult.SYSTEM_ERROR)
        self.assertTrue(any("surrounding whitespace" in str(w.message) for w in caught))

    def test_rejects_non_rule_in_rules(self):
        with self.assertRaises(TypeError):
            run_risk_assessment(DOC, ["not-a-rule"], [], [])

    def test_rejects_empty_document_id(self):
        with self.assertRaises(TypeError):
            run_risk_assessment("", [_liability_rule()], [], [])

    def test_multiple_occurrences_of_same_fact_type_each_evaluated(self):
        rule = Rule(
            rule_id="obligation-deadline", version=1, clause_pattern="obligation",
            conditions=(Condition(field="deadline_days", operator="<=", value=30),),
            severity="medium", rationale="test",
        )
        # rule fires on deadline_days <= 30 (too tight a deadline)
        f1 = MockFact("F01", DOC, "2.4", "obligation", {"deadline_days": 30})
        f2 = MockFact("F02", DOC, "2.6", "obligation", {"deadline_days": 60})
        records = run_risk_assessment(DOC, [rule], [f1, f2], [])
        self.assertEqual(len(records), 2)
        results = {r.clause_ref: r.evaluation_result for r in records}
        self.assertEqual(results["2.4"], EvaluationResult.FLAGGED)
        self.assertEqual(results["2.6"], EvaluationResult.COMPLIANT)

    def test_publish_flagged_fixture_filters_correctly(self):
        compliant = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 5_000_000})
        under_cap = MockFact("F02", DOC, "3.1", "liability_cap", {"amount": 500_000})
        records = run_risk_assessment(DOC, [_liability_rule()], [compliant], []) + \
            run_risk_assessment(DOC, [_liability_rule()], [under_cap], [])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "flagged.json"
            publish_flagged_fixture(records, out)
            data = json.loads(out.read_text())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["clause_ref"], "3.1")

    def test_publish_audit_records_includes_everything(self):
        compliant = MockFact("F01", DOC, "2.2", "liability_cap", {"amount": 5_000_000})
        records = run_risk_assessment(DOC, [_liability_rule()], [compliant], [])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audit.json"
            publish_audit_records(records, out)
            data = json.loads(out.read_text())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["evaluation_result"], "COMPLIANT")


class LoadFixtureTests(unittest.TestCase):
    def test_rejects_non_list_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"not": "a list"}))
            with self.assertRaises(TypeError):
                load_facts_fixture(path)

    def test_rejects_non_dict_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(["not-a-dict"]))
            with self.assertRaises(TypeError):
                load_facts_fixture(path)


class RealDev3FixtureTests(unittest.TestCase):
    """End-to-end sanity check against Dev 3's actual published fixtures, not just mocks."""

    def test_runs_end_to_end_against_real_dev3_data(self):
        facts = load_facts_fixture(FIXTURES / "facts.sample.json")
        edges = load_kg_edges_fixture(FIXTURES / "kg_edges.confirmed.json")

        rules = [_liability_rule()]
        records = run_risk_assessment("DOC-001", rules, facts, edges)

        self.assertEqual(len(records), 1)  # one liability_cap fact in the real fixture
        record = records[0]
        self.assertEqual(record.clause_ref, "2.2")
        # Known, still-open Dev 3 bug: value.amount is "$1,000,000", a string not a number.
        # This must surface as SYSTEM_ERROR, not crash the run and not silently pass.
        self.assertEqual(record.evaluation_result, EvaluationResult.SYSTEM_ERROR)
        self.assertIsNotNone(record.system_error)
        # confidence describes the (still real) extraction attempt even though evaluation
        # failed; there's no confirmed violation, so no triggering_fact_ids
        self.assertEqual(record.extraction_confidence, 0.96)
        self.assertEqual(record.triggering_fact_ids, ())


if __name__ == "__main__":
    unittest.main()
