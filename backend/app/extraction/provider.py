"""The LLM boundary.

Everything above this file works against `LLMProvider` and never learns whether it is
talking to a mock or to Claude. `LLM_MODE=mock` (the default) keeps fixtures and tests
deterministic; `LLM_MODE=live` swaps in the real call with no other change.
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


# --------------------------------------------------------------------------- mock


class MockProvider:
    """Deterministic stand-in, shaped around the drafting patterns in the sample contract.

    Rule-based on purpose. The point is reproducible fixtures and tests, not a second
    (worse) extraction engine — so the rules are narrow and keyed to the sample document
    rather than general. What it guarantees is that the *shapes* it emits are identical
    to what live mode emits, so nothing downstream can tell the two apart.
    """

    _MONEY = re.compile(r"\$[\d,]+(?:\.\d{2})?")
    _SECTION_REF = re.compile(r"Section (\d+(?:\.\d+)*)")
    _DAYS = re.compile(r"within (\w+) \((\d+)\) days")

    def extract_facts(self, chunk: Chunk) -> list[dict]:
        text = chunk.text
        facts: list[dict] = []

        money = self._MONEY.search(text)
        if money and "liability" in text.lower():
            facts.append(
                {
                    "fact_type": "liability_cap",
                    "value": {"amount": money.group(0), "currency": "USD"},
                    "confidence": 0.96,
                }
            )
        if "governed by" in text.lower():
            jurisdiction = re.search(r"State of ([A-Z][a-z]+(?: [A-Z][a-z]+)*)", text)
            if jurisdiction:
                facts.append(
                    {
                        "fact_type": "jurisdiction",
                        "value": {"jurisdiction": jurisdiction.group(1)},
                        "confidence": 0.97,
                    }
                )
        days = self._DAYS.search(text)
        if days:
            facts.append(
                {
                    "fact_type": "obligation",
                    "value": {
                        "obligor": "Customer",
                        "action": "pay undisputed invoices",
                        "deadline_days": int(days.group(2)),
                    },
                    "confidence": 0.93,
                }
            )
        return facts

    def extract_edges(self, chunk: Chunk) -> list[dict]:
        # The widen_context retry strategy re-sends a clause with surrounding text
        # appended after a marker. Split them so the rules below can reason about what
        # the clause itself says versus what the context adds.
        clause, _, context = chunk.text.partition(CONTEXT_MARKER)
        lower, context_lower = clause.lower(), context.lower()

        targets = [ref for ref in self._SECTION_REF.findall(clause) if ref != chunk.clause_ref]
        if not targets:
            return []
        dst = targets[0]

        # "Notwithstanding X, ... shall control" -> displaces X. Unambiguous when what
        # controls is the cap itself; when it is a schedule or process, the same phrasing
        # reads just as naturally as a modification of X's terms.
        if "notwithstanding" in lower and "shall control" in lower:
            unambiguous = "liability cap set forth in this section" in lower
            return [
                {
                    "dst_clause_ref": dst,
                    "candidate_types": ["OVERRIDES"] if unambiguous else ["OVERRIDES", "MODIFIES"],
                    "confidence": 0.94 if unambiguous else 0.58,
                    "pattern_key": "notwithstanding_shall_control",
                }
            ]

        # "shall have no claim" -> a release, or a carve-out from X's scope?
        if "no claim" in lower:
            # The defined term is carved out of X's scope and expressly not waived, so
            # the clause narrows X rather than releasing a right under it.
            if "excluded claims" in lower and "carved out of the scope" in context_lower:
                return [
                    {
                        "dst_clause_ref": dst,
                        "candidate_types": ["MODIFIES"],
                        "confidence": 0.88,
                        "pattern_key": "no_claim_in_respect_of",
                    }
                ]
            pattern = (
                "no_claim_under_section" if "no claim under section" in lower else "no_claim_in_respect_of"
            )
            return [
                {
                    "dst_clause_ref": dst,
                    "candidate_types": ["WAIVES", "MODIFIES"],
                    "confidence": 0.55,
                    "pattern_key": pattern,
                }
            ]

        # "in lieu of X ... to the extent of any inconsistency" -> genuinely unclear
        # drafting. No amount of retrieval fixes this one; that is the point of it.
        if "in lieu of" in lower:
            return [
                {
                    "dst_clause_ref": dst,
                    "candidate_types": ["OVERRIDES", "WAIVES", "MODIFIES"],
                    "confidence": 0.41,
                    "pattern_key": "in_lieu_of",
                }
            ]

        return []

    def score_parses(self, chunk: Chunk, context: str, candidates: list[EdgeType]) -> dict[str, float]:
        """Score readings from cues in the widened context.

        Mirrors what the live prompt asks for: test each reading against the surrounding
        text and say which one the language actually supports.
        """
        haystack = f"{chunk.text}\n{context}".lower()
        scores: dict[str, float] = {}
        for candidate in candidates:
            score = 0.5
            if candidate is EdgeType.WAIVES:
                # Explicit non-waiver language is strong evidence against a release.
                if "not released, waived" in haystack or "not be construed as a waiver" in haystack:
                    score -= 0.35
                if "hereby waives" in haystack or "releases and discharges" in haystack:
                    score += 0.3
            if candidate is EdgeType.MODIFIES:
                if "carved out of the scope" in haystack or "shall be read accordingly" in haystack:
                    score += 0.3
            if candidate is EdgeType.OVERRIDES:
                if "shall control" in haystack or "shall prevail" in haystack:
                    score += 0.3
            scores[candidate.value] = round(min(max(score, 0.0), 1.0), 3)
        return scores


# --------------------------------------------------------------------------- live


class ClaudeProvider:
    """Live extraction. Same interface, same output shapes."""

    def __init__(self) -> None:
        from anthropic import Anthropic  # imported lazily: optional dependency

        self._client = Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def _ask(self, system: str, user: str) -> list | dict:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        return json.loads(raw)

    def extract_facts(self, chunk: Chunk) -> list[dict]:
        return self._ask(prompts.FACTS_SYSTEM, prompts.facts_user(chunk))

    def extract_edges(self, chunk: Chunk) -> list[dict]:
        return self._ask(prompts.EDGES_SYSTEM, prompts.edges_user(chunk))

    def score_parses(self, chunk: Chunk, context: str, candidates: list[EdgeType]) -> dict[str, float]:
        return self._ask(prompts.PARSE_SYSTEM, prompts.parse_user(chunk, context, candidates))


def get_provider() -> LLMProvider:
    if settings.llm_mode == "live":
        return ClaudeProvider()
    return MockProvider()
