"""The LLM boundary for generation.

Separate from `app.extraction.provider` on purpose: §1 keeps each stage reading and
writing only what it owns, and a Clause NER prompt has no business in the Redline
Generator's blast radius.
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

    §3.4 names generation the highest hallucination risk in the system, and a
    provider's output is model output — it can be missing keys or carry a confidence
    outside [0, 1]. Parsing here is what makes the difference between a bad draft and a
    crashed run.
    """

    suggested_text: str
    rationale: str
    confidence: float


class RedlineProvider(Protocol):
    def generate(self, request: GenerationRequest) -> dict:
        """Return a raw redline dict: suggested_text, rationale, confidence."""


class CodexRedlineProvider:
    """Live generation. Same interface, same output shape as `RedlineProvider`."""

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
    return CodexRedlineProvider()
