"""Deriving a fallback ladder from the playbook.

The three rungs come out of data that already exists:

    preferred    the rule's standard_language — which is what the redline already says
    acceptable   that language relaxed along ONE member of rule.allowed_overrides
    walk_away    the counterparty's text unchanged, with what it costs spelled out

The middle rung is the interesting one, and its absence is equally interesting: a rule
with an empty `allowed_overrides` admits no concession, so the ladder is two rungs and
says so. Manufacturing a compromise the playbook does not sanction would be the single
most damaging thing this feature could do — a negotiator would concede something nobody
authorised.
"""

from __future__ import annotations

from app.negotiation.schemas import NegotiationLadder, NegotiationPosition, NegotiationTier
from app.schemas import PlaybookRule, Redline, RiskFlag, RiskSeverity

# One step down the severity ladder. A waiver narrows exposure; it does not remove it.
_SOFTER: dict[str, str] = {
    "critical": "high",
    "high": "medium",
    "medium": "low",
    "low": "low",
}

# How each override type reads as a negotiating position. Keyed on EdgeType.
_OVERRIDE_LANGUAGE: dict[str, tuple[str, str]] = {
    "OVERRIDES": (
        "provided that the parties may agree in writing that a specifically identified "
        "clause takes precedence over this one for a named, time-limited purpose",
        "a narrowly scoped, written carve-out that displaces this clause in one identified case",
    ),
    "WAIVES": (
        "provided that either party may waive this requirement in writing for a single "
        "identified transaction, such waiver not to extend beyond that transaction",
        "a single-transaction written waiver, rather than a standing exception",
    ),
    "MODIFIES": (
        "provided that the parties may vary the figures in this clause by written "
        "amendment, subject to the floor set out in the applicable schedule",
        "movement on the numbers, with the structural protection kept intact",
    ),
    "DEPENDS_ON": (
        "provided that this obligation is conditioned on the counterparty's prior "
        "performance of the obligation it depends upon",
        "conditionality rather than an unqualified obligation",
    ),
}


def _position_id(redline_id: str, tier: NegotiationTier) -> str:
    return f"NP-{redline_id}-{tier.value}"


def build_ladder(
    redline: Redline, risk: RiskFlag, rule: PlaybookRule
) -> NegotiationLadder:
    positions: list[NegotiationPosition] = []
    severity = risk.severity.value
    common = dict(
        document_id=redline.document_id,
        redline_id=redline.redline_id,
        risk_id=redline.risk_id,
        clause_ref=redline.clause_ref,
    )

    # Rung 1 — what we actually want. This is the existing redline, not a new draft;
    # re-deriving it would risk the ladder's top rung disagreeing with the approved
    # suggestion the reviewer already saw.
    positions.append(
        NegotiationPosition(
            position_id=_position_id(redline.redline_id, NegotiationTier.PREFERRED),
            tier=NegotiationTier.PREFERRED,
            rank=1,
            suggested_text=redline.suggested_text,
            rationale=rule.rationale,
            concession="None — this is the playbook position.",
            residual_severity=RiskSeverity("low"),
            confidence=redline.confidence,
            **common,
        )
    )

    overrides = list(rule.allowed_overrides or [])
    if overrides:
        # One override, not several: a rung that concedes on three axes at once is not a
        # negotiating position, it is a capitulation with extra steps.
        override = overrides[0]
        clause, concession = _OVERRIDE_LANGUAGE.get(
            override,
            (
                f"provided that the parties may agree a written {override.lower()} of this clause",
                f"a written {override.lower()} of this clause",
            ),
        )
        base = rule.standard_language or redline.suggested_text
        positions.append(
            NegotiationPosition(
                position_id=_position_id(redline.redline_id, NegotiationTier.ACCEPTABLE),
                tier=NegotiationTier.ACCEPTABLE,
                rank=2,
                suggested_text=f"{base.rstrip('. ')}, {clause}.",
                rationale=(
                    f"{rule.rule_id} v{rule.version} permits {override} as an override, so this "
                    "position stays inside the playbook."
                ),
                concession=f"Concedes {concession}.",
                residual_severity=RiskSeverity(_SOFTER.get(severity, severity)),
                grounded_in_override=override,
                confidence=round(redline.confidence * 0.9, 4),
                **common,
            )
        )

    # Rung 3 — accepting their draft, and naming the cost. `residual_severity` is the
    # rule's own severity because nothing has been mitigated: this is the position where
    # the original finding stands in full.
    positions.append(
        NegotiationPosition(
            position_id=_position_id(redline.redline_id, NegotiationTier.WALK_AWAY),
            tier=NegotiationTier.WALK_AWAY,
            rank=3,
            suggested_text=redline.original_text,
            rationale=(
                "The counterparty's language, unchanged. Included so the cost of accepting "
                "it is explicit rather than implied."
            ),
            concession=(
                f"Accepting this restores {risk.id} at {severity} severity: "
                f"{rule.rationale}"
            ),
            residual_severity=RiskSeverity(severity),
            confidence=1.0,
            **common,
        )
    )

    return NegotiationLadder(
        redline_id=redline.redline_id,
        clause_ref=redline.clause_ref,
        risk_id=redline.risk_id,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        severity=risk.severity,
        original_text=redline.original_text,
        positions=positions,
        no_fallback_available=not overrides,
    )
