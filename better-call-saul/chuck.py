"""Function 4 — Audit & Severity Logging.

Synthesizes the outputs of the rule engine (function 2, lalo.py) and the Knowledge Graph
override check (function 3, mike.py) into an immutable receipt of one rule evaluated
against one clause.

Doesn't map 1:1 onto schema.sql's risk_flags table yet — that table only has
'flagged'/'suppressed' as status values, with no room for COMPLIANT or SYSTEM_ERROR, and
a singular suppressing_edge_id where this record can hold several overrides. That's a
known, still-open gap (see the priority-matrix discussion), not something papered over
here — persisting this record is a later problem once the schema catches up.
"""

from __future__ import annotations

import datetime
import enum
import math
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from kim import Rule
from lalo import FactValue
from mike import KGEdgeLike


@enum.unique
class EvaluationResult(str, enum.Enum):
    COMPLIANT = "COMPLIANT"
    FLAGGED = "FLAGGED"
    WAIVED = "WAIVED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


def _deep_freeze_json(value: Any) -> Any:
    """Recursively validates JSON-serializability and converts mutable structures (dicts,
    lists) into immutable equivalents (MappingProxyType, tuple). NaN/Infinity are rejected
    even though Python's json module will silently emit them — neither is valid JSON."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{value!r} is not valid JSON — NaN/Infinity are not standard JSON values.")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({str(k): _deep_freeze_json(v) for k, v in value.items()})
    raise TypeError(f"Value of type {type(value).__name__} is not JSON-serializable.")


def _unfreeze(value: Any) -> Any:
    """Inverse of _deep_freeze_json — MappingProxyType/tuple back to dict/list, the only
    thing dataclasses.asdict()/json.dumps() can actually work with. Immutability was for
    in-memory safety, not for the wire format."""
    if isinstance(value, MappingProxyType):
        return {k: _unfreeze(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_unfreeze(v) for v in value]
    return value


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    timestamp: str
    document_id: str
    clause_ref: str

    evaluation_result: EvaluationResult
    final_severity: str | None
    system_error: str | None

    rule_triggered: str
    rule_version: int

    # Backed by MappingProxyType and tuples for in-memory immutability — use
    # to_json_dict() to get an actual JSON-serializable plain dict.
    fact_evaluated: Mapping[str, Any]
    kg_overrides_found: tuple[Mapping[str, str], ...]

    rationale: str
    standard_language: str | None

    def to_json_dict(self) -> dict[str, Any]:
        """The actual JSON-serializable form. dataclasses.asdict() cannot produce this on
        its own — it crashes on MappingProxyType fields (confirmed: TypeError, cannot
        pickle 'mappingproxy' object), so this exists instead of assuming asdict() works."""
        return {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp,
            "document_id": self.document_id,
            "clause_ref": self.clause_ref,
            "evaluation_result": self.evaluation_result.value,
            "final_severity": self.final_severity,
            "system_error": self.system_error,
            "rule_triggered": self.rule_triggered,
            "rule_version": self.rule_version,
            "fact_evaluated": _unfreeze(self.fact_evaluated),
            "kg_overrides_found": _unfreeze(self.kg_overrides_found),
            "rationale": self.rationale,
            "standard_language": self.standard_language,
        }


def generate_audit_record(
    document_id: str,
    clause_ref: str,
    rule: Rule,
    facts: Mapping[str, FactValue],
    rule_fired: bool,
    overrides: Sequence[KGEdgeLike] = (),
    error_message: str | None = None,
    _override_audit_id: str | None = None,
    _override_timestamp: str | None = None,
) -> AuditRecord:
    """Creates the final deterministic receipt of a rule evaluation.

    _override_audit_id / _override_timestamp exist for deterministic tests only — the
    underscore is a naming convention, not access control, so nothing stops production
    code from passing them too. Not a concern in practice: schema.sql's risk_flags.id and
    evaluated_at both have DB-level defaults (gen_random_uuid(), now()) that are
    authoritative once this is actually persisted, so whatever this function computes for
    audit_id/timestamp is provisional either way.
    """
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string.")
    if not isinstance(clause_ref, str) or not clause_ref.strip():
        raise ValueError("clause_ref must be a non-empty string.")
    if not isinstance(rule_fired, bool):
        raise TypeError(f"rule_fired must be a bool, got {type(rule_fired).__name__}")
    if error_message is not None:
        if not isinstance(error_message, str) or not error_message.strip():
            raise ValueError("error_message must be a non-empty string if provided.")

    if _override_timestamp is not None:
        if not isinstance(_override_timestamp, str):
            raise TypeError("_override_timestamp must be a string.")
        try:
            datetime.datetime.fromisoformat(_override_timestamp.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"Invalid ISO timestamp: {_override_timestamp}") from e

    if _override_audit_id is not None and not _override_audit_id.strip():
        raise ValueError("_override_audit_id cannot be empty or whitespace.")

    # Priority order: a system error outranks everything else — never let bad data pass
    # as a real compliance decision. Then: didn't fire -> compliant. Fired but excused by
    # a confirmed KG override -> waived. Fired with no excuse -> flagged, at the rule's
    # own severity (the only branch that inherits it).
    if error_message is not None:
        result = EvaluationResult.SYSTEM_ERROR
        severity = None
    elif not rule_fired:
        if overrides:
            raise ValueError("KG overrides provided but the rule did not fire.")
        result = EvaluationResult.COMPLIANT
        severity = None
    elif overrides:
        result = EvaluationResult.WAIVED
        severity = None
    else:
        result = EvaluationResult.FLAGGED
        severity = rule.severity

    validated_overrides: list[Mapping[str, str]] = []
    for o in overrides:
        try:
            edge_id = o.edge_id
            src = o.src_clause_ref
            edge_type = o.edge_type
        except AttributeError as e:
            raise TypeError("Override object is missing required KGEdgeLike attributes.") from e

        if not isinstance(edge_id, str) or not isinstance(src, str) or not isinstance(edge_type, str):
            raise TypeError(
                f"Override attributes must be strings, got {type(edge_id).__name__}, "
                f"{type(src).__name__}, {type(edge_type).__name__}."
            )

        validated_overrides.append(
            MappingProxyType({"edge_id": edge_id, "src_clause_ref": src, "edge_type": edge_type})
        )

    frozen_facts = _deep_freeze_json(dict(facts))
    now_str = _override_timestamp if _override_timestamp is not None else datetime.datetime.now(datetime.timezone.utc).isoformat()
    audit_id = _override_audit_id if _override_audit_id is not None else f"aud_{uuid.uuid4().hex}"

    return AuditRecord(
        audit_id=audit_id,
        timestamp=now_str,
        document_id=document_id.strip(),
        clause_ref=clause_ref.strip(),
        evaluation_result=result,
        final_severity=severity,
        system_error=error_message,
        rule_triggered=rule.rule_id,
        rule_version=rule.version,
        fact_evaluated=frozen_facts,
        kg_overrides_found=tuple(validated_overrides),
        rationale=rule.rationale,
        standard_language=rule.standard_language,
    )
