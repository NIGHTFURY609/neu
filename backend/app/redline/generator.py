"""The agentic active retrieval loop — ARCHITECTURE.md §4.1.

    risk flag -> seed retrieval -> [assess gaps -> targeted query] x N -> redline | escalation

§3.4 calls this the highest hallucination-risk component in the system, so the loop is
built so that it cannot write a redline it did not earn. Three exits, exactly as §4.1
specifies them:

  1. grounded, confidence clears the threshold  -> confirmed redline
  2. budget exhausted, still not grounded       -> escalation, and *no redline row at all*
  3. grounded, confidence below the threshold   -> redline held at pending_review,
                                                   escalated for approval before serving

Exit 2 is the one worth being strict about: `list_redlines` defaults to confirmed, but
"we never wrote it" is a stronger guarantee than "we wrote it and filtered it".

Every round is logged whether it closed a gap or not. That log is the retrieval trace —
it is what keeps a non-deterministic retrieval process explainable after the fact, it is
part of the audit record for the redline it produced, and Dev 1 renders it in the queue
with the same component as Clause NER's retry trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config import settings
from app.schemas import (
    EscalationReason,
    EscalationRecord,
    EscalationSource,
    PlaybookRule,
    Redline,
    ReviewStatus,
    RiskFlag,
    RiskFlagStatus,
    TraceRound,
)

from .grounding import assess
from .provider import GenerationRequest, RedlineDraft, RedlineProvider, get_provider
from .retrieval import RetrievalContext, RetrievalState


@dataclass
class RedlineResult:
    """What one risk flag produced. At most one redline, at most one escalation."""

    risk_id: str
    redline: Redline | None = None
    escalation: tuple[str, EscalationRecord] | None = None


@dataclass
class GenerationRun:
    results: list[RedlineResult] = field(default_factory=list)
    # Flags the Risk Engine already resolved against a confirmed edge. Kept rather than
    # dropped so the summary can say *why* a flag produced nothing.
    suppressed: list[RiskFlag] = field(default_factory=list)
    # Flags naming a rule version the playbook does not carry. Skipped, not escalated:
    # an absent rule is a data problem, not an ambiguity a reviewer can resolve.
    rule_not_found: list[RiskFlag] = field(default_factory=list)

    @property
    def redlines(self) -> list[Redline]:
        return [r.redline for r in self.results if r.redline is not None]

    @property
    def confirmed(self) -> list[Redline]:
        return [r for r in self.redlines if r.status is ReviewStatus.CONFIRMED]

    @property
    def escalations(self) -> list[tuple[str, str | None, EscalationRecord]]:
        """`(escalation_id, target_redline_id, record)` — the shape the store persists."""
        return [
            (escalation_id, r.redline.redline_id if r.redline else None, record)
            for r in self.results
            if r.escalation is not None
            for escalation_id, record in [r.escalation]
        ]


def _redline_id(risk: RiskFlag) -> str:
    return f"RL-{risk.id.removeprefix('RISK-')}"


def _retrieve(
    risk: RiskFlag,
    rule: PlaybookRule,
    ctx: RetrievalContext,
    budget: int,
) -> tuple[RetrievalState, list[TraceRound], bool]:
    """Run the query/evaluate/refine loop. Returns what it grounded and how."""
    state = RetrievalState()
    # The flagged clause and the facts that triggered the rule are not a search — the
    # Risk Engine handed both over with the flag. The loop starts from there.
    if (flagged := ctx.clause(risk.clause_ref)) is not None:
        state.add_chunks([flagged])
    state.facts.extend(ctx.facts_for(risk.triggering_fact_ids))

    # No budget means no active retrieval, and §4.1 does not let this stage write a
    # redline off the seed alone. Without this the loop body is skipped but the grounding
    # check below still runs, so a zero budget could serve the *most* confident output
    # instead of §7's queue-everything failure mode. `resolver.py:42` degrades the same
    # way — `STRATEGIES[:0]` is genuinely empty.
    if budget < 1:
        return state, [], False

    trace: list[TraceRound] = []
    issued: set[str] = set()

    for round_number in range(1, budget + 1):
        gaps = assess(risk, rule, ctx, state)
        if not gaps:
            break

        query = next((g for g in gaps if g.key not in issued), None)
        if query is None:
            # Gaps remain but the document offers nothing further to ask. Logged as a
            # round of its own: "we stopped early" and "we ran out of budget" are
            # different stories and the reviewer needs to see which one happened.
            trace.append(
                TraceRound(
                    round=round_number,
                    attempt="Re-evaluate grounding with no query left to issue",
                    result=(
                        "Every retrieval these gaps admit has already been tried. The "
                        "missing material is not in the document."
                    ),
                    resolved=False,
                )
            )
            break

        issued.add(query.key)
        result = query.run(ctx, state)
        grounded = not assess(risk, rule, ctx, state)
        trace.append(
            TraceRound(
                round=round_number,
                attempt=query.description,
                result=result,
                resolved=grounded,
            )
        )
        if grounded:
            break

    return state, trace, not assess(risk, rule, ctx, state)


def generate(
    risk: RiskFlag,
    rule: PlaybookRule,
    ctx: RetrievalContext,
    provider: RedlineProvider | None = None,
    budget: int | None = None,
) -> RedlineResult:
    """One risk flag through the loop."""
    if risk.status is RiskFlagStatus.SUPPRESSED:
        raise ValueError(f"{risk.id} is suppressed; the Risk Engine already cleared it")

    provider = get_provider() if provider is None else provider
    budget = settings.retrieval_budget if budget is None else budget
    redline_id = _redline_id(risk)

    state, trace, grounded = _retrieve(risk, rule, ctx, budget)

    def unwritable() -> RedlineResult:
        """§4.1 exit 2. Never write an unconfirmed redline — not even a held one."""
        return RedlineResult(
            risk_id=risk.id,
            escalation=(
                f"ESC-{redline_id}",
                EscalationRecord(
                    source=EscalationSource.REDLINE_GENERATOR,
                    reason=EscalationReason.BUDGET_EXHAUSTED,
                    document_id=risk.document_id,
                    clause_ref=risk.clause_ref,
                    rounds_attempted=len(trace),
                    trace=trace,
                ),
            ),
        )

    if not grounded:
        return unwritable()

    original = ctx.clause(risk.clause_ref)
    raw = provider.generate(
        GenerationRequest(
            risk=risk,
            rule=rule,
            original_text=original.text if original else "",
            state=state,
            rounds=len(trace),
        )
    )
    try:
        draft = RedlineDraft.model_validate(raw)
    except ValidationError:
        # A draft we cannot parse is a draft we cannot ground. Same exit as exit 2: no
        # row, one queue item. Only a malformed *shape* lands here — an implausible
        # confidence is clamped below rather than escalated, because the number is the
        # one part of the draft this stage is entitled to overrule.
        return unwritable()

    # §7 again: a rewrite is no more trustworthy than the worst page it was read off.
    # The cap goes on before the threshold gate, or a bad scan gets served as confident.
    confidence = round(min(max(draft.confidence, 0.0), 1.0, state.min_ocr_confidence), 4)
    served = confidence >= settings.redline_confidence

    redline = Redline(
        redline_id=redline_id,
        document_id=risk.document_id,
        risk_id=risk.id,
        clause_ref=risk.clause_ref,
        status=ReviewStatus.CONFIRMED if served else ReviewStatus.PENDING_REVIEW,
        confidence=confidence,
        original_text=original.text if original else "",
        suggested_text=draft.suggested_text,
        rationale=draft.rationale,
        rounds_attempted=len(trace),
        trace=trace,
        grounding_chunk_ids=[c.chunk_id for c in state.chunks],
        grounding_edge_ids=[e.edge_id for e in state.edges],
    )

    if served:
        return RedlineResult(risk_id=risk.id, redline=redline)

    # §4.1 exit 3. The redline exists but is held at pending_review until a human
    # approves it — `list_redlines` defaults to confirmed, so it is not served meanwhile.
    return RedlineResult(
        risk_id=risk.id,
        redline=redline,
        escalation=(
            f"ESC-{redline_id}",
            EscalationRecord(
                source=EscalationSource.REDLINE_GENERATOR,
                reason=EscalationReason.LOW_CONFIDENCE,
                document_id=risk.document_id,
                clause_ref=risk.clause_ref,
                rounds_attempted=len(trace),
                trace=trace,
            ),
        ),
    )


def run(
    risks: list[RiskFlag],
    playbook: list[PlaybookRule],
    ctx: RetrievalContext,
    provider: RedlineProvider | None = None,
) -> GenerationRun:
    """Every flag for a document. Suppressed flags never reach the loop.

    §3.4: the stage is triggered *by a Risk Engine flag*. A `suppressed` flag is one the
    Risk Engine already cleared against a confirmed edge, so redlining it would undo a
    decision the deterministic stage made on purpose.
    """
    provider = get_provider() if provider is None else provider
    rules = {(r.rule_id, r.version): r for r in playbook}
    out = GenerationRun()

    for risk in sorted(risks, key=lambda r: r.id):
        if risk.status is RiskFlagStatus.SUPPRESSED:
            out.suppressed.append(risk)
            continue
        # Matched on the version the flag snapshotted, not the active one — the redline
        # has to be justified against the rule that actually fired.
        rule = rules.get((risk.rule_id, risk.rule_version))
        if rule is None:
            out.rule_not_found.append(risk)
            continue
        out.results.append(generate(risk, rule, ctx, provider))

    return out
