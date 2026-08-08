"""Tests for mike.py — mock KG edges shaped like Dev 3's real KGEdge, no DB involved."""

import unittest
from dataclasses import dataclass

from kim import Condition, Rule
from mike import find_confirmed_overrides

DOC = "DOC-001"


@dataclass(frozen=True)
class MockEdge:
    """Structurally matches mike.KGEdgeLike — stands in for Dev 3's real KGEdge."""
    edge_id: str
    document_id: str
    src_clause_ref: str
    dst_clause_ref: str
    edge_type: str
    status: str


def _edge(edge_id="E01", document_id=DOC, src="4.1", dst="2.2", edge_type="OVERRIDES", status="confirmed"):
    return MockEdge(edge_id, document_id, src, dst, edge_type, status)


def _rule(allowed_overrides=("OVERRIDES",)):
    return Rule(
        rule_id="liability-cap-min",
        version=1,
        clause_pattern="liability_cap",
        conditions=(Condition(field="amount", operator="is_missing"),),  # content irrelevant, mike.py never reads it
        severity="high",
        rationale="test",
        allowed_overrides=allowed_overrides,
    )


class FindConfirmedOverridesTests(unittest.TestCase):
    def test_matches_edge_pointing_at_the_flagged_clause(self):
        edge = _edge()
        result = find_confirmed_overrides(DOC, "2.2", _rule(), [edge])
        self.assertEqual(result, (edge,))

    def test_wrong_direction_does_not_match(self):
        # edge originates FROM the flagged clause rather than pointing at it -> must not match
        edge = _edge(src="2.2", dst="4.1")
        result = find_confirmed_overrides(DOC, "2.2", _rule(), [edge])
        self.assertEqual(result, ())

    def test_pending_review_edge_does_not_match(self):
        edge = _edge(status="pending_review")
        result = find_confirmed_overrides(DOC, "2.2", _rule(), [edge])
        self.assertEqual(result, ())

    def test_edge_type_not_in_allowed_overrides_does_not_match(self):
        edge = _edge(edge_type="MODIFIES")
        result = find_confirmed_overrides(DOC, "2.2", _rule(allowed_overrides=("OVERRIDES",)), [edge])
        self.assertEqual(result, ())

    def test_empty_allowed_overrides_short_circuits(self):
        edge = _edge()
        result = find_confirmed_overrides(DOC, "2.2", _rule(allowed_overrides=()), [edge])
        self.assertEqual(result, ())

    def test_duplicate_edge_id_deduped(self):
        edge_a = _edge()
        edge_b = _edge()  # identical content, same id -> a true duplicate, not a conflict
        result = find_confirmed_overrides(DOC, "2.2", _rule(), [edge_a, edge_b])
        self.assertEqual(len(result), 1)

    def test_duplicate_edge_id_with_conflicting_data_raises(self):
        edge_a = _edge(edge_type="OVERRIDES")
        edge_b = _edge(edge_type="MODIFIES")  # same edge_id, different claim about reality
        with self.assertRaises(ValueError):
            find_confirmed_overrides(DOC, "2.2", _rule(allowed_overrides=("OVERRIDES", "MODIFIES")), [edge_a, edge_b])

    def test_multiple_distinct_matches_are_deterministically_sorted(self):
        e1 = _edge(edge_id="E02", src="5.1")
        e2 = _edge(edge_id="E01", src="4.1")
        result_a = find_confirmed_overrides(DOC, "2.2", _rule(), [e1, e2])
        result_b = find_confirmed_overrides(DOC, "2.2", _rule(), [e2, e1])
        self.assertEqual(result_a, result_b)  # order of the input must not matter

    def test_edge_from_wrong_document_raises(self):
        edge = _edge(document_id="DOC-999")
        with self.assertRaises(ValueError):
            find_confirmed_overrides(DOC, "2.2", _rule(), [edge])

    def test_rejects_non_string_clause_ref(self):
        with self.assertRaises(TypeError):
            find_confirmed_overrides(DOC, 123, _rule(), [])

    def test_rejects_empty_clause_ref(self):
        with self.assertRaises(ValueError):
            find_confirmed_overrides(DOC, "   ", _rule(), [])

    def test_rejects_empty_document_id(self):
        with self.assertRaises(TypeError):
            find_confirmed_overrides("", "2.2", _rule(), [])

    def test_rejects_malformed_edge(self):
        class NotAnEdge:
            pass
        with self.assertRaises(TypeError):
            find_confirmed_overrides(DOC, "2.2", _rule(), [NotAnEdge()])

    def test_rejects_wrong_typed_field_despite_protocol_check(self):
        edge = _edge(edge_id="")  # empty string -> still "a string" but not a valid identity key
        with self.assertRaises(TypeError):
            find_confirmed_overrides(DOC, "2.2", _rule(), [edge])

    def test_irrelevant_malformed_edge_does_not_poison_unrelated_lookup(self):
        # a bad edge_id on an edge pointing at a DIFFERENT clause must not break this lookup
        good_edge = _edge(edge_id="E01", dst="2.2")
        bad_irrelevant_edge = _edge(edge_id="", dst="9.9")
        result = find_confirmed_overrides(DOC, "2.2", _rule(), [good_edge, bad_irrelevant_edge])
        self.assertEqual(result, (good_edge,))

    def test_irrelevant_wrong_document_edge_still_raises(self):
        # document scoping is checked unconditionally, even for edges irrelevant to this clause
        bad_edge = _edge(edge_id="E99", document_id="DOC-999", dst="9.9")
        with self.assertRaises(ValueError):
            find_confirmed_overrides(DOC, "2.2", _rule(), [bad_edge])


if __name__ == "__main__":
    unittest.main()
