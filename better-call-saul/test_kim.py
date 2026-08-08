"""Tests for kim.py, run against fixtures/sample_playbook.json — no live DB needed."""

import os
import unittest

from kim import Condition, load_playbook

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_playbook.json")


class LoadPlaybookTests(unittest.TestCase):
    def test_loads_valid_playbook(self):
        rules = load_playbook(FIXTURE)
        self.assertEqual([r.rule_id for r in rules], [
            "liability-cap-min", "jurisdiction-allowed", "has-liability-clause",
        ])

    def test_list_condition_value_is_immutable_tuple(self):
        rules = load_playbook(FIXTURE)
        jurisdiction_rule = next(r for r in rules if r.rule_id == "jurisdiction-allowed")
        value = jurisdiction_rule.conditions[0].value
        self.assertIsInstance(value, tuple)

    def test_rejects_duplicate_rule_id(self):
        rules_raw = load_playbook(FIXTURE)
        # simulate via a temp file built from valid data plus a duplicate
        import json
        import tempfile
        data = [
            {"rule_id": "dup", "version": 1, "clause_pattern": "x",
             "conditions": [{"field": "a", "operator": "==", "value": 1}],
             "severity": "low", "rationale": "r"},
            {"rule_id": "dup", "version": 1, "clause_pattern": "x",
             "conditions": [{"field": "a", "operator": "==", "value": 1}],
             "severity": "low", "rationale": "r"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_playbook(path)
        finally:
            os.unlink(path)

    def test_rejects_unknown_field(self):
        import json
        import tempfile
        data = [{"rule_id": "x", "version": 1, "clause_pattern": "x",
                 "conditions": [{"field": "a", "operator": "==", "value": 1}],
                 "severity": "low", "rationale": "r", "extra": "oops"}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                load_playbook(path)
        finally:
            os.unlink(path)


class ConditionTests(unittest.TestCase):
    def test_is_missing_takes_no_value(self):
        with self.assertRaises(ValueError):
            Condition(field="x", operator="is_missing", value=1)

    def test_in_requires_non_empty_list(self):
        with self.assertRaises(ValueError):
            Condition(field="x", operator="in", value="not-a-list")
        with self.assertRaises(ValueError):
            Condition(field="x", operator="in", value=[])


if __name__ == "__main__":
    unittest.main()
