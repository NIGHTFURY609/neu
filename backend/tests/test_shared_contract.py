"""Anti-drift guard for the one field three people write.

WORK-SPLIT.md warns that a mismatch on the escalation record is *silent* — the queue
just splits into two buckets and nothing errors. So rather than copying the doc's JSON
into a test literal (which drifts the moment someone edits the doc), we parse the block
straight out of WORK-SPLIT.md and check it against the models. Edit the doc without
editing the model and this fails.

The doc's block is a schema sketch, not a valid instance: fields with several allowed
values are written `"a | b"`. We treat those as the enum's membership list.
"""

import json
import re
from pathlib import Path

import pytest

from app.schemas import (
    EscalationReason,
    EscalationRecord,
    EscalationSource,
    ReviewStatus,
    TraceRound,
)

WORK_SPLIT = Path(__file__).resolve().parents[2] / "WORK-SPLIT.md"


def _escalation_block() -> dict:
    text = WORK_SPLIT.read_text(encoding="utf-8")
    match = re.search(r"\*\*Escalation record:\*\*\s*```json\s*(\{.*?\})\s*```", text, re.S)
    assert match, "Could not find the escalation record JSON block in WORK-SPLIT.md"
    return json.loads(match.group(1))


def _alternatives(value: str) -> set[str]:
    return {part.strip() for part in value.split("|")}


def test_field_set_matches_the_doc_exactly():
    assert set(EscalationRecord.model_fields) == set(_escalation_block())


def test_trace_field_set_matches_the_doc_exactly():
    assert set(TraceRound.model_fields) == set(_escalation_block()["trace"][0])


@pytest.mark.parametrize("field, enum_cls", [("source", EscalationSource), ("reason", EscalationReason)])
def test_doc_alternatives_match_the_enum(field, enum_cls):
    assert _alternatives(_escalation_block()[field]) == {e.value for e in enum_cls}


def test_documented_escalation_record_validates():
    """The doc block, with its alternatives collapsed to one choice, is a valid record."""
    block = _escalation_block() | {"source": "clause_ner", "reason": "ambiguous_edge"}
    record = EscalationRecord.model_validate(block)

    assert record.status is ReviewStatus.PENDING_REVIEW
    assert record.rounds_attempted == 3
    assert record.trace[0].round == 1
    assert record.trace[0].resolved is False
    # Stay null until a human resolves — Dev 1 owns the write-back.
    assert record.reviewer_id is None
    assert record.resolved_at is None


def test_status_enum_is_exactly_three_lowercase_values():
    assert [s.value for s in ReviewStatus] == ["pending_review", "confirmed", "rejected"]


def test_doc_status_value_is_a_member_of_the_enum():
    assert _escalation_block()["status"] in {s.value for s in ReviewStatus}
