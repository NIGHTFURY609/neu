"""Matching risk flags and clauses to regulatory provisions.

Two mechanisms, and the response always says which one produced a match.

**Authored** — a human wrote `rule_ids: ["LIAB-CAP-FLOOR"]` into the seed fixture. That is
a citation: someone decided this statute bears on this rule and can defend it.

**Lexical** — full-text overlap. That is a suggestion, and a weaker one than it looks.

ARCHITECTURE.md §6 maps every explainability claim to a store field rather than to a
model's assertion, and this follows it: a lexical hit is never presented as a citation.
The UI renders authored matches solid and lexical ones with the dashed treatment that
already means "not decided" in this design system.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session

from app import models
from app.config import settings

# A jurisdiction match is a boost, not a filter: a New York MSA with EU data subjects
# still implicates GDPR, and hard-filtering would hide exactly that case.
_JURISDICTION_BOOST = 1.2


class RegulationMatch(BaseModel):
    provision_id: str
    citation: str
    title: str
    instrument: str
    jurisdiction: str
    summary: str
    snippet: str = ""
    source_url: str
    score: float = 0.0
    match_reason: Literal["authored", "lexical"]
    matched_on: list[str] = Field(default_factory=list)
    notes: str | None = None


class RegulatoryProvisionOut(BaseModel):
    provision_id: str
    citation: str
    title: str
    instrument: str
    instrument_title: str
    jurisdiction: str
    summary: str
    body: str
    topic_tags: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    source_url: str
    retrieved_at: str | None = None
    notes: str | None = None


def _to_match(row, *, reason, score, matched_on, snippet="") -> RegulationMatch:
    return RegulationMatch(
        provision_id=row.provision_id,
        citation=row.citation,
        title=row.title,
        instrument=row.instrument,
        jurisdiction=row.jurisdiction,
        summary=row.summary,
        snippet=snippet or row.summary,
        source_url=row.source_url,
        score=round(score, 4),
        match_reason=reason,
        matched_on=matched_on,
        notes=row.notes,
    )


def authored_matches(
    session: Session, *, rule_id: str | None, clause_pattern: str | None
) -> list[RegulationMatch]:
    """Exact, deterministic joins on the arrays a human populated."""
    conditions = []
    if rule_id:
        conditions.append(models.RegulatoryProvision.rule_ids.any(rule_id))
    if clause_pattern:
        conditions.append(models.RegulatoryProvision.clause_patterns.any(clause_pattern))
    if not conditions:
        return []

    rows = session.scalars(select(models.RegulatoryProvision).where(or_(*conditions)))
    matches = []
    for row in rows:
        matched_on = []
        if rule_id and rule_id in (row.rule_ids or []):
            matched_on.append(f"rule_id:{rule_id}")
        if clause_pattern and clause_pattern in (row.clause_patterns or []):
            matched_on.append(f"clause_pattern:{clause_pattern}")
        # A direct rule link scores 1.0; a clause-pattern-only link is weaker, because a
        # pattern is a category and a rule is a specific decision.
        score = 1.0 if any(m.startswith("rule_id:") for m in matched_on) else 0.8
        matches.append(_to_match(row, reason="authored", score=score, matched_on=matched_on))
    return matches


def lexical_matches(
    session: Session,
    *,
    query_text: str,
    jurisdiction: str | None = None,
    exclude: set[str] | None = None,
    limit: int = 6,
) -> list[RegulationMatch]:
    """Full-text overlap against the provision corpus.

    Callers build `query_text` from the playbook rule's `rationale` rather than from the
    clause body. The rationale is a short authored statement of *why the rule exists*
    ("...require an aggregate liability cap of at least $5,000,000..."); clause text is
    long boilerplate whose most frequent words are "Agreement" and "party", which match
    everything and rank nothing.
    """
    if not query_text.strip():
        return []
    exclude = exclude or set()

    tsquery = func.plainto_tsquery("english", literal(query_text))
    rank = func.ts_rank_cd(models.RegulatoryProvision.search_tsv, tsquery, 32)

    if jurisdiction:
        # Provisions of the document's own jurisdiction, and the jurisdiction-agnostic
        # ones, rank above foreign law — but foreign law still appears.
        boost = case(
            (
                models.RegulatoryProvision.jurisdiction.in_([jurisdiction, "GENERAL"]),
                _JURISDICTION_BOOST,
            ),
            else_=1.0,
        )
    else:
        boost = literal(1.0)

    score_expr = (rank * boost).label("score")
    stmt = (
        select(models.RegulatoryProvision, score_expr)
        .where(models.RegulatoryProvision.search_tsv.op("@@")(tsquery))
        .order_by(score_expr.desc())
        .limit(limit + len(exclude))
    )

    matches = []
    for row, score in session.execute(stmt):
        if row.provision_id in exclude:
            continue
        matches.append(
            _to_match(
                row,
                reason="lexical",
                score=min(float(score or 0.0), 0.79),  # never outranks an authored link
                matched_on=[f"lexical:{query_text[:60]}"],
            )
        )
        if len(matches) >= limit:
            break
    return matches


def match_risk_flag(
    session: Session, flag: models.RiskFlag, rule: models.PlaybookRule | None
) -> list[RegulationMatch]:
    authored = authored_matches(
        session,
        rule_id=flag.rule_id,
        clause_pattern=rule.clause_pattern if rule else None,
    )
    seen = {m.provision_id for m in authored}
    lexical = lexical_matches(
        session,
        query_text=rule.rationale if rule else flag.rule_id,
        exclude=seen,
        limit=max(0, settings.regulation_match_limit - len(authored)),
    )
    return sorted(authored + lexical, key=lambda m: -m.score)
