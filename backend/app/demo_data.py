"""In-memory fixture store for the local hackathon demo.

It deliberately mirrors the public API's read/resolve behaviour, allowing the Vite
frontend to exercise the full review flow without requiring credentials for Postgres.
It is activated only with ``DEMO_MODE=true``; normal deployments still use the database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.schemas import (
    EdgeType,
    EscalationItem,
    EscalationSource,
    Fact,
    KGEdge,
    Redline,
    ReviewStatus,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _read(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class DemoData:
    def __init__(self) -> None:
        self.facts = [Fact.model_validate(value) for value in _read("facts.sample.json")]
        self.edges = [KGEdge.model_validate(value) for value in _read("kg_edges.confirmed.json")]
        self.redlines = [Redline.model_validate(value) for value in _read("redline.sample.json")]
        self.escalations = [
            EscalationItem.model_validate(value)
            for name in ("escalation.sample.json", "escalation.redline.json")
            for value in _read(name)
        ]

    def list_facts(self, document_id: str) -> list[Fact]:
        return [fact for fact in self.facts if fact.document_id == document_id]

    def list_edges(self, document_id: str, status: ReviewStatus) -> list[KGEdge]:
        return [
            edge
            for edge in self.edges
            if edge.document_id == document_id and edge.status is status
        ]

    def list_redlines(self, document_id: str, status: ReviewStatus) -> list[Redline]:
        return [
            redline
            for redline in self.redlines
            if redline.document_id == document_id and redline.status is status
        ]

    def get_redline(self, redline_id: str) -> Redline | None:
        return next((redline for redline in self.redlines if redline.redline_id == redline_id), None)

    def list_escalations(
        self,
        *,
        statuses: list[ReviewStatus],
        document_id: str | None,
        source: EscalationSource | None,
    ) -> list[EscalationItem]:
        return [
            item
            for item in self.escalations
            if item.status in statuses
            and (document_id is None or item.document_id == document_id)
            and (source is None or item.source is source)
        ]

    def get_escalation(self, escalation_id: str) -> EscalationItem | None:
        return next((item for item in self.escalations if item.id == escalation_id), None)

    def resolve_escalation(
        self,
        escalation_id: str,
        *,
        status: ReviewStatus,
        reviewer_id: str,
        edge_type: EdgeType | None,
    ) -> EscalationItem:
        item = self.get_escalation(escalation_id)
        if item is None:
            raise LookupError(escalation_id)
        if item.status is not ReviewStatus.PENDING_REVIEW:
            raise ValueError(f"Escalation {escalation_id} was already {item.status.value}")

        item.status = status
        item.reviewer_id = reviewer_id
        item.resolved_at = datetime.now(UTC)

        if item.target_edge_id:
            edge = next((candidate for candidate in self.edges if candidate.edge_id == item.target_edge_id), None)
            if edge is not None:
                edge.status = status
                if edge_type:
                    edge.edge_type = edge_type
        if item.target_redline_id:
            redline = self.get_redline(item.target_redline_id)
            if redline is not None:
                redline.status = status
        return item


@lru_cache(maxsize=1)
def get_demo_data() -> DemoData:
    return DemoData()
