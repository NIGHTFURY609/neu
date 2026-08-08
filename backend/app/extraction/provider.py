"""The LLM boundary.

Everything above this file works against `LLMProvider` and never learns the mechanics
of how classification happens — only that `CodexProvider` does it via Codex, routed
through agentrouter.org (an Anthropic Messages API-compatible endpoint).
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from app.config import settings
from app.extraction import prompts
from app.schemas import Chunk, EdgeType

# Separates the clause under review from any context appended around it by the
# widen_context retry strategy, so a provider can tell which text it is classifying.
CONTEXT_MARKER = "--- CONTEXT ---"


class LLMProvider(Protocol):
    def extract_facts(self, chunk: Chunk) -> list[dict]:
        """Return raw fact dicts: fact_type, value, confidence."""

    def extract_edges(self, chunk: Chunk) -> list[dict]:
        """Return raw edge dicts: dst_clause_ref, candidate_types, confidence, pattern_key."""

    def score_parses(self, chunk: Chunk, context: str, candidates: list[EdgeType]) -> dict[str, float]:
        """Score each candidate reading against the supplied context."""


class CodexProvider:
    """Live extraction. Same interface, same output shapes as `LLMProvider`."""

    def __init__(self) -> None:
        from anthropic import Anthropic  # imported lazily: optional dependency

        # base_url points at agentrouter.org when set; omitted entirely when it is not,
        # so the SDK keeps its own default rather than being handed None.
        self._client = Anthropic(
            api_key=settings.anthropic_api_key,
            **({"base_url": settings.anthropic_base_url} if settings.anthropic_base_url else {}),
        )
        self._model = settings.anthropic_model

    def _ask(self, system: str, user: str) -> list | dict:
        # Observed directly (not theoretical): under the concurrent load `clause_ner.extract`
        # runs at, agentrouter.org occasionally returns stop_reason="end_turn" — a normal
        # completion, not a truncation — with genuinely empty text. One dropped response
        # used to fail json.loads("") and abort Clause NER for the whole document over a
        # single chunk. Retry a couple of times before giving up.
        last_error: Exception = RuntimeError("unreachable")
        for _attempt in range(3):
            message = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            raw = message.content[0].text.strip() if message.content else ""
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
            if not raw:
                last_error = ValueError("model returned an empty response")
                continue
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                last_error = exc
        raise RuntimeError(f"no parseable response after 3 attempts: {last_error}") from last_error

    def extract_facts(self, chunk: Chunk) -> list[dict]:
        return self._ask(prompts.FACTS_SYSTEM, prompts.facts_user(chunk))

    def extract_edges(self, chunk: Chunk) -> list[dict]:
        return self._ask(prompts.EDGES_SYSTEM, prompts.edges_user(chunk))

    def score_parses(self, chunk: Chunk, context: str, candidates: list[EdgeType]) -> dict[str, float]:
        return self._ask(prompts.PARSE_SYSTEM, prompts.parse_user(chunk, context, candidates))


def get_provider() -> LLMProvider:
    return CodexProvider()
