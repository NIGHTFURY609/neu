"""The LLM boundary for generation.

Separate from `app.extraction.provider` on purpose: §1 keeps each stage reading and
writing only what it owns, and a Clause NER prompt has no business in the Redline
Generator's blast radius. Same shape as that file though — `LLM_MODE=mock` (the default)
keeps fixtures and tests deterministic, `LLM_MODE=live` swaps in Claude with no other
change, and both emit identical output shapes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.config import settings
from app.schemas import EdgeType, PlaybookRule, RiskFlag

from . import prompts
from .retrieval import RetrievalState


@dataclass(frozen=True)
class GenerationRequest:
    """Everything the generator grounded, handed to whatever writes the rewrite."""

    risk: RiskFlag
    rule: PlaybookRule
    original_text: str
    state: RetrievalState
    rounds: int


class RedlineDraft(BaseModel):
    """The shape a provider must return. Nothing downstream reads the raw dict.

    §3.4 names generation the highest hallucination risk in the system, and under
    `LLM_MODE=live` a provider's output is model output — it can be missing keys or carry
    a confidence outside [0, 1]. The mock provider self-clamps, so the generator's
    unguarded reads only ever bite in production. Parsing here is what makes the
    difference between a bad draft and a crashed run.
    """

    suggested_text: str
    rationale: str
    confidence: float


class RedlineProvider(Protocol):
    def generate(self, request: GenerationRequest) -> dict:
        """Return a raw redline dict: suggested_text, rationale, confidence."""


# --------------------------------------------------------------------------- mock


class MockRedlineProvider:
    """Deterministic stand-in.

    Rule-based, like `extraction.provider.MockProvider`, and for the same reason: the
    point is reproducible fixtures and tests, not a second (worse) drafting engine. So
    the suggestion is the playbook's own `standard_language` verbatim — which is what
    makes a redline traceable to a versioned rule rather than to a model's opinion — and
    the confidence comes from two signals a reviewer can check by hand:

      - how many rounds of retrieval it took to get grounded, and
      - whether a confirmed WAIVES edge targets the clause being rewritten.

    The second one matters. A waiver of the very right the rewrite restores means the
    document may contradict the redline elsewhere; the loop can retrieve the waiver but
    it cannot tell you whether the waiver survives. That is the §4.1 exit-3 case —
    fully grounded, still not confident enough to serve — and it should cost confidence
    rather than be silently ignored.
    """

    BASE = 0.95
    PER_EXTRA_ROUND = 0.05
    PER_WAIVER = 0.20

    def generate(self, request: GenerationRequest) -> dict:
        waivers = [
            e
            for e in request.state.edges
            if e.edge_type is EdgeType.WAIVES and e.dst_clause_ref == request.risk.clause_ref
        ]
        confidence = (
            self.BASE
            - self.PER_EXTRA_ROUND * max(request.rounds - 1, 0)
            - self.PER_WAIVER * len(waivers)
        )

        grounded = ", ".join(f"Section {c.clause_ref}" for c in request.state.chunks)
        rationale = (
            f"{request.rule.rationale} Rewritten to the standard language of "
            f"{request.rule.rule_id} v{request.rule.version}. Grounded in {grounded}."
        )
        if waivers:
            waived_by = ", ".join(f"Section {e.src_clause_ref}" for e in waivers)
            rationale += (
                f" Held below the serve threshold because {waived_by} waives the right "
                f"this rewrite restores, and the document does not say whether that "
                f"waiver survives."
            )

        return {
            "suggested_text": request.rule.standard_language or request.original_text,
            "rationale": rationale,
            "confidence": round(min(max(confidence, 0.0), 1.0), 4),
        }


# --------------------------------------------------------------------------- live


class ClaudeRedlineProvider:
    """Live generation. Same interface, same output shape."""

    def __init__(self) -> None:
        from anthropic import Anthropic  # imported lazily: optional dependency

        # Same routing as app.extraction.provider — agentrouter.org when
        # ANTHROPIC_BASE_URL is set, api.anthropic.com when it is not.
        self._client = Anthropic(
            api_key=settings.anthropic_api_key,
            **({"base_url": settings.anthropic_base_url} if settings.anthropic_base_url else {}),
        )
        self._model = settings.anthropic_model

    def generate(self, request: GenerationRequest) -> dict:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=prompts.REDLINE_SYSTEM,
            messages=[{"role": "user", "content": prompts.redline_user(request)}],
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        return json.loads(raw)


def get_provider() -> RedlineProvider:
    if settings.llm_mode == "live":
        return ClaudeRedlineProvider()
    return MockRedlineProvider()
