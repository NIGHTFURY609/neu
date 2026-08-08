"""Tests for chuck.py — mock data only, no DB."""

import json
import unittest
from dataclasses import dataclass

from chuck import EvaluationResult, generate_audit_record
from kim import Condition, Rule


@dataclass(frozen=True)
class MockEdge:
    edge_id: str
    document_id: str
    src_clause_ref: str
    dst_clause_ref: str
    edge_type: str
    status: str


def _rule():
    return Rule(
        rule_id="liability-cap-min",
        version=1,
        clause_pattern="liability_cap",
        conditions=(Condition(field="amount", operator="<", value=1_000_000),),
        severity="high",
        rationale="Liability caps under $1M expose the org to uncapped downside risk.",
        allowed_overrides=("OVERRIDES",),
        standard_language="Liability shall not exceed $1,000,000 in aggregate.",
    )


class GenerateAuditRecordTests(unittest.TestCase):
    def test_compliant_when_rule_did_not_fire(self):
        record = generate_audit_record("DOC-001", "2.2", _rule(), {"amount": 500_000}, rule_fired=False)
        self.assertEqual(record.evaluation_result, EvaluationResult.COMPLIANT)
        self.assertIsNone(record.final_severity)

    def test_flagged_when_fired_with_no_overrides(self):
        record = generate_audit_record("DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True)
        self.assertEqual(record.evaluation_result, EvaluationResult.FLAGGED)
        self.assertEqual(record.final_severity, "high")

    def test_waived_when_fired_with_confirmed_override(self):
        # Confirmed against the real risk-flags fixture: a suppressed row still carries
        # severity — the violation happened, it was just legally excused, not erased.
        edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
        record = generate_audit_record(
            "DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True, overrides=[edge]
        )
        self.assertEqual(record.evaluation_result, EvaluationResult.WAIVED)
        self.assertEqual(record.final_severity, "high")
        self.assertEqual(record.kg_overrides_found[0]["edge_id"], "E01")

    def test_extraction_confidence_and_triggering_fact_ids_carried_through(self):
        record = generate_audit_record(
            "DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True,
            extraction_confidence=0.96, triggering_fact_ids=["DOC-001-C004-F01"],
        )
        self.assertEqual(record.extraction_confidence, 0.96)
        self.assertEqual(record.triggering_fact_ids, ("DOC-001-C004-F01",))
        as_json = record.to_json_dict()
        self.assertEqual(as_json["extraction_confidence"], 0.96)
        self.assertEqual(as_json["triggering_fact_ids"], ["DOC-001-C004-F01"])

    def test_rejects_out_of_range_confidence(self):
        with self.assertRaises(ValueError):
            generate_audit_record(
                "DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True, extraction_confidence=1.5
            )

    def test_rejects_non_numeric_confidence(self):
        with self.assertRaises(TypeError):
            generate_audit_record(
                "DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True, extraction_confidence="high"
            )

    def test_rejects_empty_triggering_fact_id(self):
        with self.assertRaises(TypeError):
            generate_audit_record(
                "DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True, triggering_fact_ids=[""]
            )

    def test_rejects_whitespace_padded_triggering_fact_id(self):
        with self.assertRaises(TypeError):
            generate_audit_record(
                "DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True, triggering_fact_ids=[" F01 "]
            )

    def test_system_error_outranks_everything(self):
        edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
        record = generate_audit_record(
            "DOC-001", "2.2", _rule(), {"amount": "not-a-number"},
            rule_fired=True, overrides=[edge], error_message="DataTypeError: ...",
        )
        self.assertEqual(record.evaluation_result, EvaluationResult.SYSTEM_ERROR)
        self.assertIsNone(record.final_severity)

    def test_overrides_without_rule_firing_raises(self):
        edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
        with self.assertRaises(ValueError):
            generate_audit_record("DOC-001", "2.2", _rule(), {"amount": 500_000}, rule_fired=False, overrides=[edge])

    def test_record_is_actually_json_serializable(self):
        # This is the bug that was caught before rewriting: dataclasses.asdict() on the
        # MappingProxyType-based record crashes. to_json_dict() must actually work.
        edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
        record = generate_audit_record(
            "DOC-001", "2.2", _rule(), {"amount": 5_000_000, "parties": ["ACME", "Foo LLC"]},
            rule_fired=True, overrides=[edge],
        )
        serialized = json.dumps(record.to_json_dict())
        restored = json.loads(serialized)
        self.assertEqual(restored["evaluation_result"], "WAIVED")
        self.assertEqual(restored["fact_evaluated"]["parties"], ["ACME", "Foo LLC"])

    def test_asdict_would_crash_confirms_the_bug_this_avoids(self):
        import dataclasses
        record = generate_audit_record("DOC-001", "2.2", _rule(), {"amount": 500_000}, rule_fired=False)
        with self.assertRaises(TypeError):
            dataclasses.asdict(record)

    def test_nan_fact_rejected(self):
        with self.assertRaises(ValueError):
            generate_audit_record("DOC-001", "2.2", _rule(), {"amount": float("nan")}, rule_fired=True)

    def test_deterministic_with_overrides(self):
        record = generate_audit_record(
            "DOC-001", "2.2", _rule(), {"amount": 500_000}, rule_fired=False,
            _override_audit_id="aud_fixed", _override_timestamp="2026-01-01T00:00:00Z",
        )
        self.assertEqual(record.audit_id, "aud_fixed")
        self.assertEqual(record.timestamp, "2026-01-01T00:00:00Z")

    def test_rejects_non_bool_rule_fired(self):
        with self.assertRaises(TypeError):
            generate_audit_record("DOC-001", "2.2", _rule(), {}, rule_fired="yes")

    def test_malformed_override_raises(self):
        class NotAnEdge:
            pass
        with self.assertRaises(TypeError):
            generate_audit_record("DOC-001", "2.2", _rule(), {"amount": 5_000_000}, rule_fired=True, overrides=[NotAnEdge()])


if __name__ == "__main__":
    unittest.main()
