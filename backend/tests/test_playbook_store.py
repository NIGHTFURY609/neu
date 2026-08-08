"""app.playbook_store reads `playbook_rules` rows into the two shapes the Risk Engine and
the Redline Generator each need. No database: a plain object with the same attributes as
`app.models.PlaybookRule` stands in for an ORM row, and a fake `Session.scalars` returns
it — the conversion logic is what's under test, not SQLAlchemy itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.playbook_store import load_active_playbook
from app.risk.rules import Rule
from app.schemas import PlaybookRule


@dataclass
class _FakeRow:
    rule_id: str
    version: int
    is_active: bool
    clause_pattern: str
    conditions: list
    severity: str
    rationale: str
    allowed_overrides: list = field(default_factory=list)
    standard_language: str | None = None


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self, stmt):
        return _FakeScalars(self._rows)


ROW = _FakeRow(
    rule_id="RULE-LIAB-CAP",
    version=1,
    is_active=True,
    clause_pattern="liability_cap",
    # Word-form alias, exactly as rules.load_playbook's JSON loader accepts — the DB
    # reader must translate it the same way, not assume the symbolic form was stored.
    conditions=[{"field": "amount", "operator": "lt", "value": 1000000}],
    severity="high",
    rationale="An uncapped or under-capped liability ceiling is not acceptable.",
    allowed_overrides=["WAIVES"],
    standard_language="Liability shall not exceed the fees paid in the preceding year.",
)


def test_load_active_playbook_builds_both_shapes_from_one_query():
    risk_rules, schema_rules = load_active_playbook(_FakeSession([ROW]))

    assert len(risk_rules) == 1 and len(schema_rules) == 1
    risk_rule, schema_rule = risk_rules[0], schema_rules[0]

    assert isinstance(risk_rule, Rule)
    assert risk_rule.rule_id == "RULE-LIAB-CAP"
    assert risk_rule.conditions[0].operator == "<"  # alias translated, symbolic-only in Rule
    assert risk_rule.conditions[0].value == 1000000
    assert risk_rule.allowed_overrides == ("WAIVES",)

    assert isinstance(schema_rule, PlaybookRule)
    assert schema_rule.rule_id == "RULE-LIAB-CAP"
    assert schema_rule.severity.value == "high"
    assert schema_rule.standard_language == ROW.standard_language
