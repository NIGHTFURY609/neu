"""Tests for lalo.py — pure function evaluation, no DB/KG/vector DB involved."""

import os
import unittest

from kim import Condition, Rule, load_playbook
from lalo import DataTypeError, _op_contains, _strict_member, evaluate_condition, evaluate_rule

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_playbook.json")


class OperatorTests(unittest.TestCase):
    def test_numeric_comparison(self):
        c = Condition(field="amount", operator="<", value=1_000_000)
        self.assertTrue(evaluate_condition({"amount": 500_000}, c))
        self.assertFalse(evaluate_condition({"amount": 2_000_000}, c))

    def test_string_number_mismatch_raises_not_silently_false(self):
        c = Condition(field="amount", operator="==", value=1_000_000)
        with self.assertRaises(DataTypeError):
            evaluate_condition({"amount": "1000000"}, c)

    def test_bool_int_landmine_eq(self):
        c = Condition(field="is_covered", operator="==", value=1)
        with self.assertRaises(DataTypeError):
            evaluate_condition({"is_covered": True}, c)

    def test_bool_excluded_from_ordering(self):
        c = Condition(field="amount", operator="<", value=5)
        with self.assertRaises(DataTypeError):
            evaluate_condition({"amount": True}, c)

    def test_bool_int_landmine_in(self):
        # Python's `True in [1, 2]` is True (bool is an int subclass) — must NOT match here.
        c = Condition(field="flag", operator="in", value=[1, 2])
        self.assertFalse(evaluate_condition({"flag": True}, c))

    def test_not_in_matches_tuple_from_kim(self):
        c = Condition(field="jurisdiction", operator="not_in", value=["New York", "Delaware"])
        self.assertIsInstance(c.value, tuple)  # kim.py converts list -> tuple
        self.assertTrue(evaluate_condition({"jurisdiction": "California"}, c))
        self.assertFalse(evaluate_condition({"jurisdiction": "New York"}, c))

    def test_contains_string_case_insensitive(self):
        c = Condition(field="text", operator="contains", value="Excluded Claims")
        self.assertTrue(evaluate_condition({"text": "...excluded claims arising from..."}, c))

    def test_contains_list_membership_reverse_direction(self):
        c = Condition(field="parties", operator="contains", value="ACME Corp")
        self.assertTrue(evaluate_condition({"parties": ["ACME Corp", "Foo LLC"]}, c))
        self.assertFalse(evaluate_condition({"parties": ["Foo LLC"]}, c))

    def test_is_missing_true_when_absent(self):
        c = Condition(field="amount", operator="is_missing")
        self.assertTrue(evaluate_condition({}, c))
        self.assertFalse(evaluate_condition({"amount": 5}, c))

    def test_is_missing_true_when_explicit_none(self):
        c = Condition(field="amount", operator="is_missing")
        self.assertTrue(evaluate_condition({"amount": None}, c))

    def test_missing_fact_short_circuits_non_nullary_to_false(self):
        c = Condition(field="amount", operator="<", value=1_000_000)
        self.assertFalse(evaluate_condition({}, c))

    def test_nan_rejected_in_ordering(self):
        c = Condition(field="amount", operator="<", value=1_000_000)
        with self.assertRaises(DataTypeError):
            evaluate_condition({"amount": float("nan")}, c)

    def test_nan_rejected_in_equality(self):
        c = Condition(field="amount", operator="==", value=5.0)
        with self.assertRaises(DataTypeError):
            evaluate_condition({"amount": float("nan")}, c)

    def test_none_in_collection_correctly_just_fails_to_match(self):
        # kim.py guarantees a Condition's value is never None, so this can only happen via a
        # None *entry inside* a fact list — that should just not match, not raise.
        c = Condition(field="parties", operator="contains", value="ACME Corp")
        self.assertFalse(evaluate_condition({"parties": [None, "Foo LLC"]}, c))

    def test_strict_member_rejects_none_item_directly(self):
        # kim.py's Condition can never construct a None item here, so this exercises the
        # defense-in-depth guard directly rather than through evaluate_condition/Condition.
        with self.assertRaises(DataTypeError):
            _strict_member(None, ["a", "b"])

    def test_contains_rejects_none_rule_value_directly(self):
        with self.assertRaises(DataTypeError):
            _op_contains(["ACME Corp", "Foo LLC"], None)

    def test_error_message_includes_field_context(self):
        c = Condition(field="amount", operator="==", value=5)
        with self.assertRaises(DataTypeError) as ctx:
            evaluate_condition({"amount": "5"}, c)
        self.assertIn("'amount'", str(ctx.exception))


class RuleTests(unittest.TestCase):
    def test_rule_fires_only_when_all_conditions_match(self):
        rules = load_playbook(FIXTURE)
        liability_rule = next(r for r in rules if r.rule_id == "liability-cap-min")
        self.assertTrue(evaluate_rule({"amount": 500_000}, liability_rule))
        self.assertFalse(evaluate_rule({"amount": 5_000_000}, liability_rule))

    def test_missing_liability_clause_rule(self):
        rules = load_playbook(FIXTURE)
        rule = next(r for r in rules if r.rule_id == "has-liability-clause")
        self.assertTrue(evaluate_rule({}, rule))
        self.assertFalse(evaluate_rule({"amount": 500_000}, rule))

    def test_and_rule_with_one_missing_fact_is_false(self):
        rule = load_playbook(FIXTURE)[0]  # liability-cap-min: single condition
        two_condition_rule = Rule(
            rule_id="multi-cond-test",
            version=1,
            clause_pattern=rule.clause_pattern,
            conditions=(
                Condition(field="amount", operator="<", value=1_000_000),
                Condition(field="jurisdiction", operator="==", value="New York"),
            ),
            severity="high",
            rationale="test",
        )
        # amount matches, jurisdiction fact is entirely absent -> AND must be False, not an error
        self.assertFalse(evaluate_rule({"amount": 500_000}, two_condition_rule))


if __name__ == "__main__":
    unittest.main()
