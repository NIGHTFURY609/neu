"""Summary provider. Mirrors `app/redline/provider.py` exactly."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.compare.assemble import AssembledClause
from app.config import settings
from app.schemas import Fact, KGEdge, PlaybookRule, Redline, RiskFlag


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


class ClaudeSummaryProvider:
    """Live summarization."""

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
    return ClaudeSummaryProvider()
