"""Reads the active playbook straight from the `playbook_rules` table (migration 0003) —
the DB-backed counterpart to `app.risk.rules.load_playbook`'s JSON-file loader. The table
has existed since 0003 but nothing read it; every real run went through a fixture file.

Both downstream stages need the *same* active rule set, in two different shapes: the Risk
Engine's own `Rule` dataclass (`app.risk.rules`, what `run_risk_assessment` takes), and
the pydantic `app.schemas.PlaybookRule` the Redline Generator was built against. One query
builds both, so a rule can't disagree with itself between the two stages of one run.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.risk.rules import OPERATOR_ALIASES, Condition, Rule
from app.schemas import PlaybookRule


def _conditions(raw: list[dict]) -> tuple[Condition, ...]:
    # Same alias translation as rules.load_playbook, applied here too: a row written with
    # the word-form operators (`lt`, `eq`, ...) must not fail Condition's symbolic-only
    # validation.
    return tuple(
        Condition(**{**c, "operator": OPERATOR_ALIASES.get(c["operator"], c["operator"])})
        for c in raw
    )


def load_active_playbook(
    session: Session, jurisdiction: str | None = None
) -> tuple[list[Rule], list[PlaybookRule]]:
    """The active playbook, as both stages need it. Superseded (`is_active=False`)
    versions are excluded, same as `rules.load_playbook`.

    `jurisdiction` narrows to rules scoped to it plus rules scoped nowhere in particular
    (`jurisdiction IS NULL`, meaning "applies everywhere"). None returns every active
    rule, which is what every caller before revision 0004 wanted.

    The filter lives here rather than on `risk.rules.Rule` on purpose: that dataclass
    validates against a closed `KNOWN_RULE_FIELDS` set and is pinned by a large test
    surface, while `run_risk_assessment` is already pure over whatever rule list it is
    handed. Narrowing the query therefore needs no change to the engine at all — which
    is also what makes "evaluate this contract under another jurisdiction's playbook"
    nearly free.
    """
    stmt = select(models.PlaybookRule).where(models.PlaybookRule.is_active.is_(True))
    if jurisdiction is not None:
        stmt = stmt.where(
            (models.PlaybookRule.jurisdiction.is_(None))
            | (models.PlaybookRule.jurisdiction == jurisdiction)
        )
    rows = list(session.scalars(stmt))

    risk_rules = [
        Rule(
            rule_id=row.rule_id,
            version=row.version,
            clause_pattern=row.clause_pattern,
            conditions=_conditions(row.conditions),
            severity=row.severity,
            rationale=row.rationale,
            allowed_overrides=tuple(row.allowed_overrides or []),
            standard_language=row.standard_language,
        )
        for row in rows
    ]
    schema_rules = [
        PlaybookRule.model_validate(
            {
                "rule_id": row.rule_id,
                "version": row.version,
                "is_active": row.is_active,
                "clause_pattern": row.clause_pattern,
                "conditions": row.conditions,
                "severity": row.severity,
                "rationale": row.rationale,
                "allowed_overrides": row.allowed_overrides or [],
                "standard_language": row.standard_language,
            }
        )
        for row in rows
    ]
    return risk_rules, schema_rules
