"""Bounded retry before escalation.

ARCHITECTURE.md §3.2 as written sends a human every ambiguity, including ones the agent
could have settled itself; §7 names that over-escalation as the risk that turns the
system into a queue-everything machine. So an ambiguous candidate runs the strategies in
`strategies.STRATEGIES` first, and only reaches the Review Queue if the budget runs out.

Every round is logged whether it succeeds or not. That log is the retry trace: it goes
into the audit trail, and it is what lets a reviewer tell "nobody has looked at this yet"
from "the agent already tried three things and failed".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.schemas import CandidateEdge, EdgeType, Provenance, TraceRound

from .strategies import STRATEGIES, ResolutionContext


@dataclass(frozen=True)
class Resolution:
    resolved: bool
    trace: list[TraceRound]
    edge_type: EdgeType | None = None
    confidence: float = 0.0
    resolved_by: Provenance | None = None

    @property
    def rounds_attempted(self) -> int:
        return len(self.trace)


def resolve(
    candidate: CandidateEdge,
    ctx: ResolutionContext,
    budget: int | None = None,
) -> Resolution:
    """Run up to `budget` strategies against an ambiguous candidate edge."""
    budget = settings.retry_budget if budget is None else budget
    trace: list[TraceRound] = []

    for round_number, (name, strategy) in enumerate(STRATEGIES[:budget], start=1):
        outcome = strategy(candidate, ctx)
        trace.append(
            TraceRound(
                round=round_number,
                attempt=name,
                result=outcome.summary,
                resolved=outcome.resolved,
            )
        )
        if outcome.resolved:
            return Resolution(
                resolved=True,
                trace=trace,
                edge_type=outcome.edge_type,
                confidence=outcome.confidence,
                resolved_by=Provenance(f"retry:{name}"),
            )

    return Resolution(resolved=False, trace=trace)
