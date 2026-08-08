"""Prompt for live generation.

Asks for bare JSON in exactly the shape `MockRedlineProvider` produces, so nothing
downstream can tell the two apart.

The user message carries only text the retrieval loop actually grounded. That is the
point of §4.1 — the model does not get the document, it gets the clauses the agent
proved it needed, and every one of them is named in the retrieval trace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoids a cycle: provider imports this module
    from .provider import GenerationRequest

REDLINE_SYSTEM = """You rewrite a single contract clause to bring it into compliance
with one versioned playbook rule.

You are given the clause, the rule that flagged it, the rule's standard language, and
the surrounding clauses and definitions a retrieval agent has grounded. Use only that
material. Do not invent defined terms, cross-references, or facts that are not present.

Return a bare JSON object:
  {"suggested_text": the rewritten clause, in full,
   "rationale": why this rewrite satisfies the rule, naming the grounded clauses you
                relied on,
   "confidence": float 0-1}

Lower the confidence when the grounded material leaves the rewrite in tension with
another clause — a waiver or override of the right you are restoring, for instance. A
low confidence is handled correctly downstream; a confidently wrong redline is not."""


def redline_user(request: "GenerationRequest") -> str:
    grounded = "\n\n".join(
        f"Section {c.clause_ref} ({c.section_type}):\n{c.text}" for c in request.state.chunks
    )
    edges = (
        "\n".join(
            f"Section {e.src_clause_ref} {e.edge_type.value} Section {e.dst_clause_ref}"
            for e in request.state.edges
        )
        or "none"
    )
    facts = "\n".join(f"{f.fact_type.value}: {f.value}" for f in request.state.facts) or "none"

    return (
        f"Clause under review — Section {request.risk.clause_ref}:\n"
        f"{request.original_text}\n\n"
        f"Rule {request.rule.rule_id} v{request.rule.version} ({request.rule.severity.value}): "
        f"{request.rule.rationale}\n"
        f"Standard language: {request.rule.standard_language or 'none supplied'}\n\n"
        f"Facts that triggered the flag:\n{facts}\n\n"
        f"Confirmed relationships bearing on this clause:\n{edges}\n\n"
        f"Grounded clauses:\n{grounded}"
    )
