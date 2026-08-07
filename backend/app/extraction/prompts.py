"""Prompts for live extraction.

Each asks for bare JSON in exactly the shape MockProvider produces, so nothing
downstream can tell the two apart.
"""

from app.schemas import Chunk, EdgeType

FACTS_SYSTEM = """You extract structured legal facts from a single contract clause.

Return a JSON array. Each element:
  {"fact_type": one of liability_cap|jurisdiction|date|monetary_value|obligation|party,
   "value": object with the extracted values,
   "confidence": float 0-1}

Return [] if the clause states no extractable fact. Output bare JSON, no prose."""

EDGES_SYSTEM = """You identify structural relationships between contract clauses.

Given one clause, find references to other clauses and classify the relationship the
clause bears to each. Return a JSON array. Each element:
  {"dst_clause_ref": string,
   "candidate_types": array of OVERRIDES|DEPENDS_ON|WAIVES|MODIFIES,
   "confidence": float 0-1,
   "pattern_key": short snake_case name for the drafting pattern you matched}

Do not guess. If the language genuinely supports more than one reading, list every
plausible type in candidate_types and lower the confidence — an ambiguous answer is
handled correctly downstream, a confidently wrong one is not.

Output bare JSON, no prose."""

PARSE_SYSTEM = """You test competing readings of an ambiguous contract clause.

You are given a clause, additional surrounding context, and candidate relationship
types. Score how well each reading is supported by the language, 0-1.

Weigh the supplied context heavily — it usually contains the disambiguating term. If the
context does not separate the readings, score them close together rather than picking a
winner arbitrarily.

Return a bare JSON object mapping each candidate type to its score."""


def facts_user(chunk: Chunk) -> str:
    return f"Clause {chunk.clause_ref}:\n{chunk.text}"


def edges_user(chunk: Chunk) -> str:
    return f"Clause {chunk.clause_ref}:\n{chunk.text}"


def parse_user(chunk: Chunk, context: str, candidates: list[EdgeType]) -> str:
    options = ", ".join(c.value for c in candidates)
    return (
        f"Clause {chunk.clause_ref}:\n{chunk.text}\n\n"
        f"Surrounding context:\n{context}\n\n"
        f"Candidate readings: {options}"
    )
