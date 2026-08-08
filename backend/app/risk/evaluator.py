"""Rule evaluation engine — Dev 4's `lalo.py`, ported from `better-call-saul/`.

Evaluates extracted facts against a Rule's conditions (ANDed). Strictly
deterministic and strongly typed — no DB, no Knowledge Graph, no Vector DB.
KG override/waiver checks are `overrides.py`, a separate stage that runs on
whatever this module flags. This module never touches raw text or
embeddings, full stop (see ARCHITECTURE.md §3.3).
"""

import math
from typing import TypeAlias

from app.risk.rules import Condition, NULLARY_OPERATORS, Rule, Scalar

FactValue: TypeAlias = Scalar | list[Scalar]         # facts arrive as JSON-parsed values — plain lists
RuleValue: TypeAlias = Scalar | list[Scalar] | None  # matches rules.py's declared Condition.value type.
# rules.py normalizes list values to a real tuple at runtime (object.__setattr__ in __post_init__), but
# the field's static type stays list[Scalar] since callers construct it from parsed JSON lists — this
# alias mirrors that declared contract rather than the runtime-only tuple, so isinstance(x, (list, tuple))
# checks below still cover both. None only ever appears for nullary operators — rules.py's Condition
# rejects None for anything else.


class DataTypeError(TypeError):
    """Raised when fact/rule values are incompatible with the operator — never silently coerced."""


def _type_family(value: object) -> str:
    """int and float share the "numeric" family — bool is its own family even though
    Python makes bool an int subclass (checked first, below, so it doesn't fall into numeric)."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "numeric"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "list"
    return "unknown"


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _strict_member(item: FactValue | RuleValue, collection: list[Scalar] | tuple[Scalar, ...]) -> bool:
    """Membership check where bool and numeric never compare equal (Python's `True == 1` doesn't
    apply), and an item of an unrecognized type (e.g. None) raises rather than silently matching
    nothing — a null "contains"/"in" target is a caller bug, not a legitimate non-match."""
    item_family = _type_family(item)
    if item_family == "unknown":
        raise DataTypeError(f"cannot check membership of {item!r} — unsupported type {type(item).__name__}")
    return any(_type_family(candidate) == item_family and candidate == item for candidate in collection)


def _op_eq(fact: FactValue, rule: RuleValue) -> bool:
    fact_family = _type_family(fact)
    if fact_family != _type_family(rule):
        raise DataTypeError(f"'==' requires matching types, got {type(fact).__name__} vs {type(rule).__name__}")
    if fact_family == "numeric" and (_is_nan(fact) or _is_nan(rule)):
        raise DataTypeError("NaN cannot be compared meaningfully.")
    return fact == rule


def _op_ne(fact: FactValue, rule: RuleValue) -> bool:
    return not _op_eq(fact, rule)


def _numeric(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataTypeError(f"{label} must be numeric (not bool/str/list), got {type(value).__name__}")
    if math.isnan(value):
        raise DataTypeError(f"{label} is NaN, which cannot be compared meaningfully.")
    return value


def _op_lt(fact: FactValue, rule: RuleValue) -> bool:
    return _numeric(fact, "fact") < _numeric(rule, "rule")


def _op_le(fact: FactValue, rule: RuleValue) -> bool:
    return _numeric(fact, "fact") <= _numeric(rule, "rule")


def _op_gt(fact: FactValue, rule: RuleValue) -> bool:
    return _numeric(fact, "fact") > _numeric(rule, "rule")


def _op_ge(fact: FactValue, rule: RuleValue) -> bool:
    return _numeric(fact, "fact") >= _numeric(rule, "rule")


def _op_in(fact: FactValue, rule: RuleValue) -> bool:
    if not isinstance(rule, (list, tuple)):
        raise DataTypeError(f"'in' requires rule to be a list, got {type(rule).__name__}")
    return _strict_member(fact, rule)


def _op_not_in(fact: FactValue, rule: RuleValue) -> bool:
    return not _op_in(fact, rule)


def _op_contains(fact: FactValue, rule: RuleValue) -> bool:
    """String fact: case-insensitive substring match. List fact: type-strict membership
    (the reverse direction of `in` — `in` checks fact-in-rule-list, `contains` checks
    rule-value-in-fact-list, e.g. "parties contains 'ACME Corp'")."""
    if isinstance(fact, str):
        if not isinstance(rule, str):
            raise DataTypeError(f"'contains' on a string fact requires a string rule value, got {type(rule).__name__}")
        return rule.lower() in fact.lower()
    if isinstance(fact, (list, tuple)):
        return _strict_member(rule, fact)
    raise DataTypeError(f"'contains' requires fact to be a string or list, got {type(fact).__name__}")


OPERATOR_DISPATCH = {
    "==": _op_eq,
    "!=": _op_ne,
    "<": _op_lt,
    "<=": _op_le,
    ">": _op_gt,
    ">=": _op_ge,
    "in": _op_in,
    "not_in": _op_not_in,
    "contains": _op_contains,
}


def _evaluate_operator(fact_value: FactValue, op_string: str, rule_value: RuleValue) -> bool:
    if op_string not in OPERATOR_DISPATCH:
        raise ValueError(f"Operator {op_string!r} cannot be evaluated against values (is it nullary, e.g. is_missing?).")
    op_func = OPERATOR_DISPATCH[op_string]
    try:
        return op_func(fact_value, rule_value)
    except DataTypeError:
        raise  # already the specific error — don't let the generic TypeError catch below re-wrap it
    except TypeError as e:
        raise DataTypeError(
            f"Incompatible types for {op_string!r}: {type(fact_value).__name__} vs {type(rule_value).__name__}"
        ) from e


def evaluate_condition(facts: dict[str, FactValue], condition: Condition) -> bool:
    """A fact counts as missing if the key is absent OR its value is None (rules.py's spec).

    For ordinary (non-nullary) operators, a missing fact makes the condition return False —
    not an error and not a match. Only is_missing/is_present look at absence itself; every
    other operator treats "no fact" the same as "condition not satisfied."
    """
    fact_present = condition.field in facts and facts[condition.field] is not None

    if condition.operator in NULLARY_OPERATORS:
        return (not fact_present) if condition.operator == "is_missing" else fact_present

    if not fact_present:
        return False  # missing required fact -> this condition (and its rule) does not trigger

    try:
        return _evaluate_operator(facts[condition.field], condition.operator, condition.value)
    except DataTypeError as e:
        raise DataTypeError(f"field {condition.field!r}: {e}") from e


def evaluate_rule(facts: dict[str, FactValue], rule: Rule) -> bool:
    """True if every condition matches (ANDed) — the rule fires for these facts."""
    try:
        return all(evaluate_condition(facts, condition) for condition in rule.conditions)
    except DataTypeError as e:
        raise DataTypeError(f"rule {rule.rule_id!r}: {e}") from e
