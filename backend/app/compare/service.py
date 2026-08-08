"""Assembling a ComparisonResult from two documents' stored rows."""

from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app import models
from app.compare.align import Pairing, align_clauses, normalize
from app.compare.assemble import PREAMBLE_REF, AssembledClause, assemble_clauses
from app.compare.schemas import (
    ClauseAlignment,
    ComparisonResult,
    ComparisonTotals,
    DocumentRef,
    FactDelta,
    RedlineOutcome,
    RiskDelta,
)
from app.compare.textdiff import word_diff
from app.config import settings
from app.kg import store as kg_store
from app.redline import store as redline_store
from app.risk import store as risk_store
from app.schemas import Fact, RiskFlag

# Below this, the pairing is a guess and the UI marks it as one.
LOW_CONFIDENCE_ALIGNMENT = 0.70
# Above this similarity, a "modified" clause is really unchanged prose.
IDENTICAL = 0.999


def _document_ref(row: models.Document) -> DocumentRef:
    return DocumentRef(
        document_id=row.document_id,
        filename=row.filename,
        title=row.title,
        version=row.version,
        jurisdiction=row.jurisdiction,
        uploaded_at=row.uploaded_at,
    )


def _clause_status(pairing: Pairing) -> str:
    if pairing.left_ref is None:
        return "added"
    if pairing.right_ref is None:
        return "removed"
    if pairing.similarity >= IDENTICAL:
        # Same words under a different number is a renumbering, not an edit — worth
        # distinguishing, because it is the change a reader can safely skip.
        return "identical" if pairing.left_ref == pairing.right_ref else "renumbered"
    return "modified"


def _risk_deltas(
    left_flags: list[RiskFlag],
    right_flags: list[RiskFlag],
    left_to_right: dict[str, str],
) -> list[RiskDelta]:
    """Match flags across documents on `(rule_id, aligned clause)`.

    Not on `clause_ref`: a renumbered clause would otherwise report its risk as resolved
    on the left and appeared on the right, doubling the noise exactly where a reader is
    trying to see whether anything really changed.
    """
    right_by_key: dict[tuple[str, str], RiskFlag] = {
        (flag.rule_id, flag.clause_ref): flag for flag in right_flags
    }
    deltas: list[RiskDelta] = []
    seen: set[tuple[str, str]] = set()

    for flag in left_flags:
        mapped_ref = left_to_right.get(flag.clause_ref)
        counterpart = right_by_key.get((flag.rule_id, mapped_ref)) if mapped_ref else None
        if counterpart is not None:
            seen.add((counterpart.rule_id, counterpart.clause_ref))

        if counterpart is None:
            deltas.append(
                RiskDelta(
                    change="resolved",
                    rule_id=flag.rule_id,
                    left_risk_id=flag.id,
                    left_ref=flag.clause_ref,
                    right_ref=mapped_ref,
                    left_severity=flag.severity,
                    left_status=flag.status,
                    note=(
                        "the clause was removed"
                        if mapped_ref is None
                        else f"clause {mapped_ref} no longer triggers this rule"
                    ),
                )
            )
            continue

        common = dict(
            rule_id=flag.rule_id,
            left_risk_id=flag.id,
            right_risk_id=counterpart.id,
            left_ref=flag.clause_ref,
            right_ref=counterpart.clause_ref,
            left_severity=flag.severity,
            right_severity=counterpart.severity,
            left_status=flag.status,
            right_status=counterpart.status,
        )
        if flag.status != counterpart.status:
            # flagged -> suppressed means a confirmed KG edge now waives it. A pure text
            # diff cannot see this at all: the clause may be word-for-word identical and
            # the finding still gone, because what changed was somewhere else entirely.
            note = (
                f"now suppressed by {counterpart.suppressing_edge_id or 'a confirmed edge'}"
                if counterpart.status.value == "suppressed"
                else "no longer waived — the suppressing edge is gone"
            )
            deltas.append(RiskDelta(change="suppression_changed", note=note, **common))
        elif flag.severity != counterpart.severity:
            deltas.append(
                RiskDelta(
                    change="severity_changed",
                    note=f"{flag.severity.value} -> {counterpart.severity.value}",
                    **common,
                )
            )
        else:
            deltas.append(RiskDelta(change="unchanged", **common))

    for flag in right_flags:
        if (flag.rule_id, flag.clause_ref) in seen:
            continue
        deltas.append(
            RiskDelta(
                change="appeared",
                rule_id=flag.rule_id,
                right_risk_id=flag.id,
                right_ref=flag.clause_ref,
                right_severity=flag.severity,
                right_status=flag.status,
                note="new finding in this version",
            )
        )
    return deltas


def _redline_outcomes(
    left_redlines,
    left_to_right: dict[str, str],
    right_clauses: dict[str, AssembledClause],
) -> list[RedlineOutcome]:
    """Did the text we suggested actually end up in the other document?"""
    outcomes: list[RedlineOutcome] = []
    for redline in left_redlines:
        target_ref = left_to_right.get(redline.clause_ref)
        if target_ref is None or target_ref not in right_clauses:
            outcomes.append(
                RedlineOutcome(
                    redline_id=redline.redline_id,
                    clause_ref=redline.clause_ref,
                    outcome="clause_removed",
                )
            )
            continue
        target = right_clauses[target_ref]
        similarity = SequenceMatcher(
            None,
            normalize(redline.suggested_text),
            normalize(target.text, target.heading),
            autojunk=False,
        ).ratio()
        if similarity >= settings.redline_applied_threshold:
            outcome = "applied"
        elif similarity >= settings.align_match_threshold:
            outcome = "partially_applied"
        else:
            outcome = "not_applied"
        outcomes.append(
            RedlineOutcome(
                redline_id=redline.redline_id,
                clause_ref=redline.clause_ref,
                outcome=outcome,
                similarity=round(similarity, 4),
                right_ref=target_ref,
            )
        )
    return outcomes


def _fact_deltas(
    left_facts: list[Fact], right_facts: list[Fact], left_to_right: dict[str, str]
) -> list[FactDelta]:
    """Cheap and unusually legible: "$1,000,000 -> $750,000", "New York -> Delaware"."""
    right_by_key = {(f.fact_type, f.clause_ref): f for f in right_facts}
    deltas: list[FactDelta] = []
    seen: set[tuple] = set()

    for fact in left_facts:
        mapped = left_to_right.get(fact.clause_ref)
        counterpart = right_by_key.get((fact.fact_type, mapped)) if mapped else None
        if counterpart is None:
            deltas.append(
                FactDelta(
                    fact_type=fact.fact_type,
                    left_ref=fact.clause_ref,
                    left_value=fact.value,
                    change="removed",
                )
            )
            continue
        seen.add((counterpart.fact_type, counterpart.clause_ref))
        deltas.append(
            FactDelta(
                fact_type=fact.fact_type,
                left_ref=fact.clause_ref,
                right_ref=counterpart.clause_ref,
                left_value=fact.value,
                right_value=counterpart.value,
                change="unchanged" if fact.value == counterpart.value else "changed",
            )
        )

    for fact in right_facts:
        if (fact.fact_type, fact.clause_ref) not in seen:
            deltas.append(
                FactDelta(
                    fact_type=fact.fact_type,
                    right_ref=fact.clause_ref,
                    right_value=fact.value,
                    change="added",
                )
            )
    return deltas


def compare_documents(
    session: Session,
    left_row: models.Document,
    right_row: models.Document,
    *,
    diff_detail: str = "changed_only",
) -> ComparisonResult:
    left_clauses = assemble_clauses(kg_store.list_chunks(session, left_row.document_id))
    right_clauses = assemble_clauses(kg_store.list_chunks(session, right_row.document_id))
    pairings = align_clauses(left_clauses, right_clauses)

    left_to_right = {p.left_ref: p.right_ref for p in pairings if p.left_ref and p.right_ref}

    alignments: list[ClauseAlignment] = []
    totals = ComparisonTotals()
    for pairing in pairings:
        status = _clause_status(pairing)
        clause = (
            left_clauses.get(pairing.left_ref)
            if pairing.left_ref
            else right_clauses.get(pairing.right_ref)
        )
        diff = None
        if diff_detail != "none" and (diff_detail == "all" or status != "identical"):
            left_text = left_clauses[pairing.left_ref].text if pairing.left_ref else ""
            right_text = right_clauses[pairing.right_ref].text if pairing.right_ref else ""
            diff = word_diff(left_text, right_text)

        alignments.append(
            ClauseAlignment(
                left_ref=pairing.left_ref,
                right_ref=pairing.right_ref,
                heading=clause.heading if clause else "",
                status=status,
                alignment_score=pairing.score,
                similarity=pairing.similarity,
                diff=diff,
            )
        )
        if status in {"identical", "renumbered"}:
            totals.clauses_identical += 1
        elif status == "modified":
            totals.clauses_modified += 1
        elif status == "added":
            totals.clauses_added += 1
        else:
            totals.clauses_removed += 1

    left_flags = risk_store.list_risk_flags(session, left_row.document_id)
    right_flags = risk_store.list_risk_flags(session, right_row.document_id)
    risk_deltas = _risk_deltas(left_flags, right_flags, left_to_right)
    for delta in risk_deltas:
        if delta.change == "appeared":
            totals.risks_appeared += 1
        elif delta.change == "resolved":
            totals.risks_resolved += 1
        elif delta.change == "severity_changed":
            totals.risks_severity_changed += 1

    caveats: list[str] = []
    if PREAMBLE_REF in left_clauses or PREAMBLE_REF in right_clauses:
        caveats.append(
            "Some text carries no clause number and is compared as a single block; "
            "changes inside it are reported coarsely."
        )
    guessed = [
        p for p in pairings
        if p.left_ref and p.right_ref and p.score < LOW_CONFIDENCE_ALIGNMENT
    ]
    if guessed:
        caveats.append(
            f"{len(guessed)} clause pairing(s) scored below {LOW_CONFIDENCE_ALIGNMENT:.2f} "
            "and may be mismatched: "
            + ", ".join(f"{p.left_ref}~{p.right_ref}" for p in guessed[:5])
        )
    worst = min(
        (c.min_ocr_confidence for c in {**left_clauses, **right_clauses}.values()),
        default=1.0,
    )
    if worst < 0.75:
        caveats.append(
            f"Worst page OCR confidence across both documents is {worst:.2f}; "
            "differences on that page may be scanning artefacts rather than edits."
        )

    # `contract_family` is what makes two documents versions of each other. Derived here
    # rather than asked of the caller, so the label cannot disagree with the data.
    same_family = (
        left_row.contract_family is not None
        and left_row.contract_family == right_row.contract_family
    )

    return ComparisonResult(
        left=_document_ref(left_row),
        right=_document_ref(right_row),
        pairing="version" if same_family else "cross_document",
        clauses=alignments,
        risk_deltas=risk_deltas,
        redline_outcomes=_redline_outcomes(
            redline_store.list_redlines(session, left_row.document_id),
            left_to_right,
            right_clauses,
        ),
        fact_deltas=_fact_deltas(
            kg_store.list_facts(session, left_row.document_id),
            kg_store.list_facts(session, right_row.document_id),
            left_to_right,
        ),
        totals=totals,
        caveats=caveats,
    )
