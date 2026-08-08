"""Summary providers. Mirrors `app/redline/provider.py` exactly."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.compare.assemble import PREAMBLE_REF, AssembledClause
from app.config import settings
from app.schemas import Fact, KGEdge, PlaybookRule, Redline, RiskFlag

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class SummaryRequest:
    document_id: str
    filename: str
    clauses: dict[str, AssembledClause]
    facts: list[Fact]
    risks: list[RiskFlag]
    rules: dict[tuple[str, int], PlaybookRule]
    edges: list[KGEdge]
    redlines: list[Redline]
    # Every id a claim is allowed to cite. Built from the rows above, and enforced after
    # generation by `validate.enforce_citations`.
    citable: frozenset[str] = field(default_factory=frozenset)


class SummaryProvider(Protocol):
    def summarize(self, request: SummaryRequest) -> dict: ...


def _claim(text: str, sources: list[tuple[str, str]]) -> dict:
    return {"text": text, "sources": [{"kind": k, "ref": r} for k, r in sources]}


class MockSummaryProvider:
    """Rule-based over the extracted facts. Deterministic, and the default.

    Not a second, worse model — the same reasoning as `MockRedlineProvider`. Every
    sentence here is assembled from a stored value, so the mock output satisfies the
    citation contract by construction rather than by luck, and the fixtures it produces
    are stable enough to pin in a test.
    """

    name = "mock"

    def summarize(self, request: SummaryRequest) -> dict:
        by_type: dict[str, list[Fact]] = {}
        for fact in request.facts:
            by_type.setdefault(fact.fact_type.value, []).append(fact)

        out: dict = {
            "parties": [],
            "key_obligations": [],
            "unusual_clauses": [],
            "top_risks": [],
        }

        for fact in by_type.get("party", []):
            name = fact.value.get("name", "an unnamed party")
            role = fact.value.get("role", "party")
            out["parties"].append(
                _claim(
                    f"{name} ({role}).",
                    [("fact", fact.fact_id), ("clause", fact.clause_ref)],
                )
            )

        jurisdictions = by_type.get("jurisdiction", [])
        if jurisdictions:
            fact = jurisdictions[0]
            out["governing_law"] = _claim(
                f"Governed by the laws of {fact.value.get('jurisdiction', 'an unstated jurisdiction')}.",
                [("fact", fact.fact_id), ("clause", fact.clause_ref)],
            )

        caps = by_type.get("liability_cap", [])
        if caps:
            fact = caps[0]
            # `amount_text` is the original matched string, kept by
            # `extraction.clause_ner._coerce_money` precisely so a human-facing sentence
            # can say "$1,000,000" rather than "1000000".
            shown = fact.value.get("amount_text") or fact.value.get("amount")
            text = f"Aggregate liability is capped at {shown}."
            sources = [("fact", fact.fact_id), ("clause", fact.clause_ref)]
            if len(caps) > 1:
                others = ", ".join(
                    str(f.value.get("amount_text") or f.value.get("amount")) for f in caps[1:]
                )
                text += f" A further cap is stated elsewhere ({others}); the two must be read together."
                sources.extend(("fact", f.fact_id) for f in caps[1:])
            out["liability_cap"] = _claim(text, sources)

        for fact in by_type.get("obligation", []):
            obligor = fact.value.get("obligor", "A party")
            action = fact.value.get("action", "perform")
            days = fact.value.get("deadline_days")
            sentence = (
                f"{obligor} shall {action} within {days} days."
                if days is not None
                else f"{obligor} shall {action}."
            )
            claim = _claim(sentence, [("fact", fact.fact_id), ("clause", fact.clause_ref)])
            out["key_obligations"].append(claim)
            if days is not None and out.get("payment") is None and "pay" in action.lower():
                out["payment"] = claim

        for clause_ref, clause in request.clauses.items():
            heading = clause.heading.lower()
            if any(word in heading for word in ("term", "terminat")):
                target = "termination" if "terminat" in heading else "term"
                if out.get(target) is None:
                    out[target] = _claim(
                        f"{clause.heading or f'Clause {clause_ref}'} governs {target}.",
                        [("clause", clause_ref)],
                    )

        flagged = sorted(
            (r for r in request.risks if r.status.value == "flagged"),
            key=lambda r: _SEVERITY_ORDER.get(r.severity.value, 9),
        )[:5]
        for risk in flagged:
            rule = request.rules.get((risk.rule_id, risk.rule_version))
            out["top_risks"].append(
                {
                    "risk_id": risk.id,
                    "clause_ref": risk.clause_ref,
                    "severity": risk.severity.value,
                    "rule_id": risk.rule_id,
                    "rule_version": risk.rule_version,
                    # The playbook rationale verbatim: authored text, not generated. The
                    # summary reports what the rule says, it does not reinterpret it.
                    "statement": _claim(
                        rule.rationale if rule else f"Violates {risk.rule_id}.",
                        [("risk", risk.id), ("clause", risk.clause_ref)],
                    ),
                }
            )

        for edge in request.edges:
            if edge.edge_type.value in {"OVERRIDES", "WAIVES"}:
                out["unusual_clauses"].append(
                    _claim(
                        f"Clause {edge.src_clause_ref} {edge.edge_type.value.lower()} "
                        f"clause {edge.dst_clause_ref}.",
                        [("edge", edge.edge_id), ("clause", edge.src_clause_ref)],
                    )
                )
        if PREAMBLE_REF in request.clauses:
            out["unusual_clauses"].append(
                _claim(
                    "Some text carries no clause number and could not be attributed.",
                    [("clause", PREAMBLE_REF)],
                )
            )
        return out


class ClaudeSummaryProvider:
    """Live summarization. Same shape and the same fence-stripping as the redline path."""

    name = "claude"

    def __init__(self) -> None:
        from anthropic import Anthropic

        kwargs = {"api_key": settings.anthropic_api_key}
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        self._client = Anthropic(**kwargs)
        self._model = settings.anthropic_model

    def summarize(self, request: SummaryRequest) -> dict:
        from app.summary.prompts import SUMMARY_SYSTEM, summary_user

        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": summary_user(request)}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        return json.loads(raw)


def get_provider() -> SummaryProvider:
    if settings.llm_mode == "live":
        return ClaudeSummaryProvider()
    return MockSummaryProvider()
