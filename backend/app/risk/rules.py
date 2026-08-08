"""Playbook rule schema + storage — Dev 4's `kim.py`, ported from `better-call-saul/`.

Owns the shape of a compliance rule and how a playbook (a list of rules) is
loaded. Deterministic and structured on purpose — the Risk Engine (`evaluator.py`)
can only be reproducible if the playbook itself is data, not prose.

A playbook is the *current active* ruleset, not a version history — one entry
per rule_id. `version` stamps which revision was active when a clause was
evaluated (for the audit trail); it does not mean two versions of the same
rule can coexist in one playbook file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

Scalar = str | int | float | bool

SEVERITIES = frozenset({"low", "medium", "high", "critical"})
OPERATORS = frozenset({"==", "!=", "<", "<=", ">", ">=", "in", "not_in", "contains", "is_missing", "is_present"})
SET_OPERATORS = frozenset({"in", "not_in"})
NULLARY_OPERATORS = frozenset({"is_missing", "is_present"})  # act on fact presence, not fact value

# Word-form aliases accepted at the JSON-loading boundary only — `fixtures/playbook.sample.json`
# (the shared, repo-root fixture the redline generator also reads) was written with `lt`/`eq`
# rather than the symbolic vocabulary below. The validated core (OPERATORS, Condition) stays
# symbolic-only; translation happens once, here, before a Condition is ever constructed.
OPERATOR_ALIASES = {
    "eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=",
}

# Operator semantics (evaluated by the risk engine, `evaluator.py` — written down here
# since the schema is meaningless without them):
#   ==, !=       : both sides must be the same type family, bool counted as its own
#                  family separate from numeric — a numeric fact compared against a
#                  string condition (or a bool compared against 1) raises, never
#                  silently compares unequal or uses Python's `True == 1`.
#   <, <=, >, >= : numeric only. bool is excluded even though Python treats it as an
#                  int subtype — "liability_cap < true" is not a meaningful comparison.
#   in, not_in   : list membership, type-strict — bool and int are never treated as
#                  equal (Python's `True == 1` does not apply here).
#   contains     : direction matters, this is not in/not_in reversed-and-renamed.
#                  in/not_in check fact-value-in-rule-list (e.g. jurisdiction in
#                  [...]). contains checks the opposite direction: string fact ->
#                  case-insensitive substring match against a string rule value;
#                  list fact -> type-strict membership of the rule value within the
#                  fact list (e.g. parties contains "ACME Corp").
#   is_missing,
#   is_present   : a fact counts as "missing" if the key is absent from the extracted
#                  facts OR its value is explicitly None/null — both mean "no value",
#                  not two different states.

KNOWN_RULE_FIELDS = frozenset({
    "rule_id", "version", "clause_pattern", "conditions",
    "severity", "rationale", "allowed_overrides", "standard_language",
    # Not a Rule field — playbook_rules keeps full version history (app.models.PlaybookRule),
    # and fixtures/playbook.sample.json is written in that shape. load_playbook() accepts
    # and reads it below, but a Rule itself has no is_active concept: per the module
    # docstring, "a playbook is the current active ruleset" — an inactive version simply
    # isn't in the file being loaded.
    "is_active",
})


def _clean_str(label: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty string with no surrounding whitespace, got {value!r}")
    return value


@dataclass(frozen=True)
class Condition:
    field: str
    operator: str
    value: Scalar | list[Scalar] | None = None

    def __post_init__(self) -> None:
        _clean_str("Condition field", self.field)
        if self.operator not in OPERATORS:
            raise ValueError(f"Invalid operator: {self.operator!r}")
        if self.operator in NULLARY_OPERATORS:
            if self.value is not None:
                raise ValueError(f"{self.operator!r} does not take a value.")
            return
        value = self.value
        if value is None:
            raise ValueError("Condition value cannot be None.")
        is_list = isinstance(value, list)
        if self.operator in SET_OPERATORS and not (is_list and value):
            raise ValueError(f"{self.operator!r} requires a non-empty list value.")
        if self.operator not in SET_OPERATORS and is_list:
            raise ValueError(f"{self.operator!r} does not accept a list value.")
        if isinstance(value, list):
            object.__setattr__(self, "value", tuple(value))  # close the same mutability gap, one level down


@dataclass(frozen=True)
class Rule:
    rule_id: str                # stable id, e.g. "liability-cap-min"
    version: int                # bumped on any change to conditions/severity
    clause_pattern: str         # which fact type this rule applies to, e.g. "liability_cap"
    conditions: tuple[Condition, ...]         # ANDed
    severity: str               # one of SEVERITIES
    rationale: str              # legal reasoning — shown to reviewers and fed to redline
    allowed_overrides: tuple[str, ...] = field(default_factory=tuple)  # confirmed KG edge types that legitimately waive this rule
    standard_language: str | None = None                               # preferred/fallback wording, for redline to ground against

    def __post_init__(self) -> None:
        _clean_str("rule_id", self.rule_id)
        _clean_str("clause_pattern", self.clause_pattern)
        if self.version < 1:
            raise ValueError(f"{self.rule_id}: version must be >= 1.")
        if not self.rationale.strip():
            raise ValueError(f"{self.rule_id}: rationale cannot be empty.")
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.rule_id}: severity must be one of {sorted(SEVERITIES)}.")
        if not self.conditions:
            raise ValueError(f"{self.rule_id}: needs at least one condition.")
        for override in self.allowed_overrides:
            _clean_str(f"{self.rule_id}: allowed_overrides entry", override)
        if self.standard_language is not None:
            _clean_str(f"{self.rule_id}: standard_language", self.standard_language)


def load_playbook(path: str) -> list[Rule]:
    """Load a playbook from a JSON file: a list of rule objects matching Rule's fields.

    Condition operators may use either the symbolic vocabulary (OPERATORS) or the
    word-form aliases in OPERATOR_ALIASES (`lt`, `eq`, ...) — translated here, once,
    before construction, so Condition's own validation stays symbolic-only.
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise TypeError("Playbook JSON root must be a list of rules.")

    rules: list[Rule] = []
    seen_ids: set[str] = set()

    for i, entry in enumerate(raw):
        try:
            if not isinstance(entry, dict):
                raise TypeError(f"rule must be a JSON object, got {type(entry).__name__}")
            unknown = entry.keys() - KNOWN_RULE_FIELDS
            if unknown:
                raise ValueError(f"unknown field(s): {sorted(unknown)}")
            if not entry.get("is_active", True):
                continue  # superseded version, not part of the active playbook

            conditions = tuple(
                Condition(**{**c, "operator": OPERATOR_ALIASES.get(c["operator"], c["operator"])})
                for c in entry.get("conditions", [])
            )
            rule = Rule(
                rule_id=entry["rule_id"],
                version=entry["version"],
                clause_pattern=entry["clause_pattern"],
                conditions=conditions,
                severity=entry["severity"],
                rationale=entry["rationale"],
                allowed_overrides=tuple(entry.get("allowed_overrides", [])),
                standard_language=entry.get("standard_language"),
            )
            if rule.rule_id in seen_ids:
                raise ValueError(f"duplicate rule_id: {rule.rule_id}")
            seen_ids.add(rule.rule_id)
            rules.append(rule)
        except (KeyError, ValueError, TypeError) as e:
            entry_id = entry.get("rule_id", "UNKNOWN") if isinstance(entry, dict) else "UNKNOWN"
            raise ValueError(f"Failed to load rule at index {i} (id={entry_id}): {e}") from e

    return rules
