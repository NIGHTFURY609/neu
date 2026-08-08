"""Tests for `app.risk.rules`, `app.risk.evaluator`, `app.risk.overrides` and
`app.risk.audit` — ported from `better-call-saul/test_kim.py`, `test_lalo.py`,
`test_mike.py` and `test_chuck.py`. Pure logic, no DB.
"""

import json
from dataclasses import dataclass

import pytest

from app.risk.audit import EvaluationResult, generate_audit_record
from app.risk.evaluator import DataTypeError, _op_contains, _strict_member, evaluate_condition, evaluate_rule
from app.risk.overrides import find_confirmed_overrides
from app.risk.rules import Condition, Rule, load_playbook

SAMPLE_PLAYBOOK = [
    {
        "rule_id": "liability-cap-min", "version": 1, "clause_pattern": "liability_cap",
        "conditions": [{"field": "amount", "operator": "<", "value": 1000000}],
        "severity": "high",
        "rationale": "Liability caps under $1M expose the org to uncapped downside risk.",
        "allowed_overrides": ["EXECUTIVE_APPROVAL"],
        "standard_language": "Liability shall not exceed $1,000,000 in aggregate.",
    },
    {
        "rule_id": "jurisdiction-allowed", "version": 1, "clause_pattern": "jurisdiction",
        "conditions": [{"field": "jurisdiction", "operator": "not_in", "value": ["New York", "Delaware"]}],
        "severity": "medium",
        "rationale": "Non-standard jurisdictions require legal review.",
    },
    {
        "rule_id": "has-liability-clause", "version": 1, "clause_pattern": "liability_cap",
        "conditions": [{"field": "amount", "operator": "is_missing"}],
        "severity": "critical",
        "rationale": "Contracts must contain an explicit liability cap clause.",
    },
]


@pytest.fixture
def playbook_path(tmp_path):
    path = tmp_path / "playbook.json"
    path.write_text(json.dumps(SAMPLE_PLAYBOOK), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------- rules.py


def test_loads_valid_playbook(playbook_path):
    rules = load_playbook(playbook_path)
    assert [r.rule_id for r in rules] == [
        "liability-cap-min", "jurisdiction-allowed", "has-liability-clause",
    ]


def test_list_condition_value_is_immutable_tuple(playbook_path):
    rules = load_playbook(playbook_path)
    jurisdiction_rule = next(r for r in rules if r.rule_id == "jurisdiction-allowed")
    assert isinstance(jurisdiction_rule.conditions[0].value, tuple)


def test_rejects_duplicate_rule_id(tmp_path):
    data = [
        {"rule_id": "dup", "version": 1, "clause_pattern": "x",
         "conditions": [{"field": "a", "operator": "==", "value": 1}],
         "severity": "low", "rationale": "r"},
        {"rule_id": "dup", "version": 1, "clause_pattern": "x",
         "conditions": [{"field": "a", "operator": "==", "value": 1}],
         "severity": "low", "rationale": "r"},
    ]
    path = tmp_path / "dup.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_playbook(path)


def test_rejects_unknown_field(tmp_path):
    data = [{"rule_id": "x", "version": 1, "clause_pattern": "x",
             "conditions": [{"field": "a", "operator": "==", "value": 1}],
             "severity": "low", "rationale": "r", "extra": "oops"}]
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_playbook(path)


def test_operator_aliases_translate_to_symbolic(tmp_path):
    """fixtures/playbook.sample.json (the repo-root fixture) uses word-form operators —
    the alias table must translate them before Condition ever sees them."""
    data = [{"rule_id": "x", "version": 1, "clause_pattern": "liability_cap",
             "conditions": [{"field": "amount", "operator": "lt", "value": 5000000}],
             "severity": "high", "rationale": "r"}]
    path = tmp_path / "aliased.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    rules = load_playbook(path)
    assert rules[0].conditions[0].operator == "<"


def test_is_missing_takes_no_value():
    with pytest.raises(ValueError):
        Condition(field="x", operator="is_missing", value=1)


def test_in_requires_non_empty_list():
    with pytest.raises(ValueError):
        Condition(field="x", operator="in", value="not-a-list")
    with pytest.raises(ValueError):
        Condition(field="x", operator="in", value=[])


# -------------------------------------------------------------------------- evaluator.py


def test_numeric_comparison():
    c = Condition(field="amount", operator="<", value=1_000_000)
    assert evaluate_condition({"amount": 500_000}, c) is True
    assert evaluate_condition({"amount": 2_000_000}, c) is False


def test_string_number_mismatch_raises_not_silently_false():
    c = Condition(field="amount", operator="==", value=1_000_000)
    with pytest.raises(DataTypeError):
        evaluate_condition({"amount": "1000000"}, c)


def test_bool_int_landmine_eq():
    c = Condition(field="is_covered", operator="==", value=1)
    with pytest.raises(DataTypeError):
        evaluate_condition({"is_covered": True}, c)


def test_bool_excluded_from_ordering():
    c = Condition(field="amount", operator="<", value=5)
    with pytest.raises(DataTypeError):
        evaluate_condition({"amount": True}, c)


def test_bool_int_landmine_in():
    # Python's `True in [1, 2]` is True (bool is an int subclass) — must NOT match here.
    c = Condition(field="flag", operator="in", value=[1, 2])
    assert evaluate_condition({"flag": True}, c) is False


def test_not_in_matches_tuple_from_rules():
    c = Condition(field="jurisdiction", operator="not_in", value=["New York", "Delaware"])
    assert isinstance(c.value, tuple)  # rules.py converts list -> tuple
    assert evaluate_condition({"jurisdiction": "California"}, c) is True
    assert evaluate_condition({"jurisdiction": "New York"}, c) is False


def test_contains_string_case_insensitive():
    c = Condition(field="text", operator="contains", value="Excluded Claims")
    assert evaluate_condition({"text": "...excluded claims arising from..."}, c) is True


def test_contains_list_membership_reverse_direction():
    c = Condition(field="parties", operator="contains", value="ACME Corp")
    assert evaluate_condition({"parties": ["ACME Corp", "Foo LLC"]}, c) is True
    assert evaluate_condition({"parties": ["Foo LLC"]}, c) is False


def test_is_missing_true_when_absent():
    c = Condition(field="amount", operator="is_missing")
    assert evaluate_condition({}, c) is True
    assert evaluate_condition({"amount": 5}, c) is False


def test_is_missing_true_when_explicit_none():
    c = Condition(field="amount", operator="is_missing")
    assert evaluate_condition({"amount": None}, c) is True


def test_missing_fact_short_circuits_non_nullary_to_false():
    c = Condition(field="amount", operator="<", value=1_000_000)
    assert evaluate_condition({}, c) is False


def test_nan_rejected_in_ordering():
    c = Condition(field="amount", operator="<", value=1_000_000)
    with pytest.raises(DataTypeError):
        evaluate_condition({"amount": float("nan")}, c)


def test_nan_rejected_in_equality():
    c = Condition(field="amount", operator="==", value=5.0)
    with pytest.raises(DataTypeError):
        evaluate_condition({"amount": float("nan")}, c)


def test_none_in_collection_correctly_just_fails_to_match():
    # rules.py guarantees a Condition's value is never None, so this can only happen via a
    # None *entry inside* a fact list — that should just not match, not raise.
    c = Condition(field="parties", operator="contains", value="ACME Corp")
    assert evaluate_condition({"parties": [None, "Foo LLC"]}, c) is False


def test_strict_member_rejects_none_item_directly():
    with pytest.raises(DataTypeError):
        _strict_member(None, ["a", "b"])


def test_contains_rejects_none_rule_value_directly():
    with pytest.raises(DataTypeError):
        _op_contains(["ACME Corp", "Foo LLC"], None)


def test_error_message_includes_field_context():
    c = Condition(field="amount", operator="==", value=5)
    with pytest.raises(DataTypeError, match="'amount'"):
        evaluate_condition({"amount": "5"}, c)


def test_rule_fires_only_when_all_conditions_match(playbook_path):
    rules = load_playbook(playbook_path)
    liability_rule = next(r for r in rules if r.rule_id == "liability-cap-min")
    assert evaluate_rule({"amount": 500_000}, liability_rule) is True
    assert evaluate_rule({"amount": 5_000_000}, liability_rule) is False


def test_missing_liability_clause_rule(playbook_path):
    rules = load_playbook(playbook_path)
    rule = next(r for r in rules if r.rule_id == "has-liability-clause")
    assert evaluate_rule({}, rule) is True
    assert evaluate_rule({"amount": 500_000}, rule) is False


def test_and_rule_with_one_missing_fact_is_false():
    rule = Rule(
        rule_id="multi-cond-test", version=1, clause_pattern="liability_cap",
        conditions=(
            Condition(field="amount", operator="<", value=1_000_000),
            Condition(field="jurisdiction", operator="==", value="New York"),
        ),
        severity="high", rationale="test",
    )
    # amount matches, jurisdiction fact is entirely absent -> AND must be False, not an error
    assert evaluate_rule({"amount": 500_000}, rule) is False


# -------------------------------------------------------------------------- overrides.py

DOC = "DOC-001"


@dataclass(frozen=True)
class MockEdge:
    """Structurally matches overrides.KGEdgeLike — stands in for the real KGEdge."""
    edge_id: str
    document_id: str
    src_clause_ref: str
    dst_clause_ref: str
    edge_type: str
    status: str


def _edge(edge_id="E01", document_id=DOC, src="4.1", dst="2.2", edge_type="OVERRIDES", status="confirmed"):
    return MockEdge(edge_id, document_id, src, dst, edge_type, status)


def _override_rule(allowed_overrides=("OVERRIDES",)):
    return Rule(
        rule_id="liability-cap-min", version=1, clause_pattern="liability_cap",
        conditions=(Condition(field="amount", operator="is_missing"),),  # content irrelevant here
        severity="high", rationale="test", allowed_overrides=allowed_overrides,
    )


def test_matches_edge_pointing_at_the_flagged_clause():
    edge = _edge()
    assert find_confirmed_overrides(DOC, "2.2", _override_rule(), [edge]) == (edge,)


def test_wrong_direction_does_not_match():
    # edge originates FROM the flagged clause rather than pointing at it -> must not match
    edge = _edge(src="2.2", dst="4.1")
    assert find_confirmed_overrides(DOC, "2.2", _override_rule(), [edge]) == ()


def test_pending_review_edge_does_not_match():
    edge = _edge(status="pending_review")
    assert find_confirmed_overrides(DOC, "2.2", _override_rule(), [edge]) == ()


def test_edge_type_not_in_allowed_overrides_does_not_match():
    edge = _edge(edge_type="MODIFIES")
    assert find_confirmed_overrides(DOC, "2.2", _override_rule(allowed_overrides=("OVERRIDES",)), [edge]) == ()


def test_empty_allowed_overrides_short_circuits():
    edge = _edge()
    assert find_confirmed_overrides(DOC, "2.2", _override_rule(allowed_overrides=()), [edge]) == ()


def test_duplicate_edge_id_deduped():
    edge_a, edge_b = _edge(), _edge()  # identical content, same id -> a true duplicate
    result = find_confirmed_overrides(DOC, "2.2", _override_rule(), [edge_a, edge_b])
    assert len(result) == 1


def test_duplicate_edge_id_with_conflicting_data_raises():
    edge_a = _edge(edge_type="OVERRIDES")
    edge_b = _edge(edge_type="MODIFIES")  # same edge_id, different claim about reality
    with pytest.raises(ValueError):
        find_confirmed_overrides(DOC, "2.2", _override_rule(allowed_overrides=("OVERRIDES", "MODIFIES")), [edge_a, edge_b])


def test_multiple_distinct_matches_are_deterministically_sorted():
    e1 = _edge(edge_id="E02", src="5.1")
    e2 = _edge(edge_id="E01", src="4.1")
    result_a = find_confirmed_overrides(DOC, "2.2", _override_rule(), [e1, e2])
    result_b = find_confirmed_overrides(DOC, "2.2", _override_rule(), [e2, e1])
    assert result_a == result_b  # order of the input must not matter


def test_edge_from_wrong_document_raises():
    edge = _edge(document_id="DOC-999")
    with pytest.raises(ValueError):
        find_confirmed_overrides(DOC, "2.2", _override_rule(), [edge])


def test_rejects_non_string_clause_ref():
    with pytest.raises(TypeError):
        find_confirmed_overrides(DOC, 123, _override_rule(), [])


def test_rejects_empty_clause_ref():
    with pytest.raises(ValueError):
        find_confirmed_overrides(DOC, "   ", _override_rule(), [])


def test_rejects_empty_document_id():
    with pytest.raises(TypeError):
        find_confirmed_overrides("", "2.2", _override_rule(), [])


def test_rejects_malformed_edge():
    class NotAnEdge:
        pass
    with pytest.raises(TypeError):
        find_confirmed_overrides(DOC, "2.2", _override_rule(), [NotAnEdge()])


def test_irrelevant_malformed_edge_does_not_poison_unrelated_lookup():
    # a bad edge_id on an edge pointing at a DIFFERENT clause must not break this lookup
    good_edge = _edge(edge_id="E01", dst="2.2")
    bad_irrelevant_edge = _edge(edge_id="", dst="9.9")
    result = find_confirmed_overrides(DOC, "2.2", _override_rule(), [good_edge, bad_irrelevant_edge])
    assert result == (good_edge,)


def test_irrelevant_wrong_document_edge_still_raises():
    # document scoping is checked unconditionally, even for edges irrelevant to this clause
    bad_edge = _edge(edge_id="E99", document_id="DOC-999", dst="9.9")
    with pytest.raises(ValueError):
        find_confirmed_overrides(DOC, "2.2", _override_rule(), [bad_edge])


# ------------------------------------------------------------------------------ audit.py


def _audit_rule():
    return Rule(
        rule_id="liability-cap-min", version=1, clause_pattern="liability_cap",
        conditions=(Condition(field="amount", operator="<", value=1_000_000),),
        severity="high",
        rationale="Liability caps under $1M expose the org to uncapped downside risk.",
        allowed_overrides=("OVERRIDES",),
        standard_language="Liability shall not exceed $1,000,000 in aggregate.",
    )


def test_compliant_when_rule_did_not_fire():
    record = generate_audit_record("DOC-001", "2.2", _audit_rule(), {"amount": 500_000}, rule_fired=False)
    assert record.evaluation_result == EvaluationResult.COMPLIANT
    assert record.final_severity is None


def test_flagged_when_fired_with_no_overrides():
    record = generate_audit_record("DOC-001", "2.2", _audit_rule(), {"amount": 5_000_000}, rule_fired=True)
    assert record.evaluation_result == EvaluationResult.FLAGGED
    assert record.final_severity == "high"


def test_waived_when_fired_with_confirmed_override():
    edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
    record = generate_audit_record(
        "DOC-001", "2.2", _audit_rule(), {"amount": 5_000_000}, rule_fired=True, overrides=[edge]
    )
    assert record.evaluation_result == EvaluationResult.WAIVED
    assert record.final_severity is None
    assert record.kg_overrides_found[0]["edge_id"] == "E01"


def test_system_error_outranks_everything():
    edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
    record = generate_audit_record(
        "DOC-001", "2.2", _audit_rule(), {"amount": "not-a-number"},
        rule_fired=True, overrides=[edge], error_message="DataTypeError: ...",
    )
    assert record.evaluation_result == EvaluationResult.SYSTEM_ERROR
    assert record.final_severity is None


def test_overrides_without_rule_firing_raises():
    edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
    with pytest.raises(ValueError):
        generate_audit_record("DOC-001", "2.2", _audit_rule(), {"amount": 500_000}, rule_fired=False, overrides=[edge])


def test_record_is_actually_json_serializable():
    # dataclasses.asdict() on the MappingProxyType-based record crashes — to_json_dict()
    # must actually work.
    edge = MockEdge("E01", "DOC-001", "4.1", "2.2", "OVERRIDES", "confirmed")
    record = generate_audit_record(
        "DOC-001", "2.2", _audit_rule(), {"amount": 5_000_000, "parties": ["ACME", "Foo LLC"]},
        rule_fired=True, overrides=[edge],
    )
    restored = json.loads(json.dumps(record.to_json_dict()))
    assert restored["evaluation_result"] == "WAIVED"
    assert restored["fact_evaluated"]["parties"] == ["ACME", "Foo LLC"]


def test_asdict_would_crash_confirms_the_bug_this_avoids():
    import dataclasses
    record = generate_audit_record("DOC-001", "2.2", _audit_rule(), {"amount": 500_000}, rule_fired=False)
    with pytest.raises(TypeError):
        dataclasses.asdict(record)


def test_nan_fact_rejected():
    with pytest.raises(ValueError):
        generate_audit_record("DOC-001", "2.2", _audit_rule(), {"amount": float("nan")}, rule_fired=True)


def test_deterministic_with_overrides():
    record = generate_audit_record(
        "DOC-001", "2.2", _audit_rule(), {"amount": 500_000}, rule_fired=False,
        _override_audit_id="aud_fixed", _override_timestamp="2026-01-01T00:00:00Z",
    )
    assert record.audit_id == "aud_fixed"
    assert record.timestamp == "2026-01-01T00:00:00Z"


def test_rejects_non_bool_rule_fired():
    with pytest.raises(TypeError):
        generate_audit_record("DOC-001", "2.2", _audit_rule(), {}, rule_fired="yes")


def test_malformed_override_raises():
    class NotAnEdge:
        pass
    with pytest.raises(TypeError):
        generate_audit_record("DOC-001", "2.2", _audit_rule(), {"amount": 5_000_000}, rule_fired=True, overrides=[NotAnEdge()])
