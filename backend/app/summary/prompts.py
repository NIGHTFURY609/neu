"""Prompt construction for live summarization.

String building, no templating library — same as `app/extraction/prompts.py` and
`app/redline/prompts.py`.
"""

from __future__ import annotations

import json

from app.summary.provider import SummaryRequest

SUMMARY_SYSTEM = """You summarize a commercial contract for a reviewing lawyer.

You are given extracted structured data, not the document. Everything you write must be
traceable to a row you were given.

Rules:
- Every statement is an object {"text": ..., "sources": [{"kind": ..., "ref": ...}]}.
- `kind` is one of clause, fact, risk, redline, edge. `ref` is the id you were given.
- Cite only ids that appear in the CITABLE IDS list. Inventing an id is worse than
  omitting a statement: a statement with no source is dropped, but a fabricated citation
  looks checkable and is not.
- If the data does not support a field, omit it. Do not infer standard commercial terms
  that are not present — an absent liability cap is a finding, not something to fill in.
- For top_risks, use the supplied rule rationale verbatim as the statement text. Do not
  reword it. It is authored legal language and its precision is the point.
- Reply with bare JSON. No prose, no markdown fences.

Shape:
{"parties": [Claim], "term": Claim|null, "key_obligations": [Claim],
 "payment": Claim|null, "liability_cap": Claim|null, "termination": Claim|null,
 "governing_law": Claim|null, "unusual_clauses": [Claim],
 "top_risks": [{"risk_id","clause_ref","severity","rule_id","rule_version","statement":Claim}]}"""


def summary_user(request: SummaryRequest) -> str:
    """Assemble the prompt body.

    Full clause text is sent only for clauses carrying a flagged risk — at most a handful.
    Everything else is a one-line index entry. Sending the whole document would be both
    wasteful and counterproductive: it invites the model to summarize prose it was not
    asked to trust, when the extracted rows are the reviewed data.
    """
    flagged_refs = {r.clause_ref for r in request.risks if r.status.value == "flagged"}

    clause_index = [
        {
            "clause_ref": ref,
            "heading": clause.heading,
            "section_type": clause.section_type,
            "preview": clause.text[:160],
        }
        for ref, clause in sorted(request.clauses.items())
    ]
    flagged_bodies = {
        ref: request.clauses[ref].text for ref in sorted(flagged_refs) if ref in request.clauses
    }

    facts = [
        {
            "fact_id": f.fact_id,
            "clause_ref": f.clause_ref,
            "fact_type": f.fact_type.value,
            "value": f.value,
            "confidence": f.confidence,
            "provenance": f.provenance.value,
        }
        for f in request.facts
    ]
    risks = [
        {
            "risk_id": r.id,
            "clause_ref": r.clause_ref,
            "rule_id": r.rule_id,
            "rule_version": r.rule_version,
            "severity": r.severity.value,
            "status": r.status.value,
            "rationale": (
                request.rules[(r.rule_id, r.rule_version)].rationale
                if (r.rule_id, r.rule_version) in request.rules
                else None
            ),
        }
        for r in request.risks
    ]
    edges = [
        {
            "edge_id": e.edge_id,
            "src": e.src_clause_ref,
            "dst": e.dst_clause_ref,
            "type": e.edge_type.value,
        }
        for e in request.edges
    ]
    redlines = [
        {
            "redline_id": r.redline_id,
            "clause_ref": r.clause_ref,
            "status": r.status.value,
            "rationale": r.rationale,
        }
        for r in request.redlines
    ]

    return "\n\n".join(
        [
            f"DOCUMENT: {request.document_id} ({request.filename})",
            f"CLAUSE INDEX:\n{json.dumps(clause_index, indent=2)}",
            f"FULL TEXT OF FLAGGED CLAUSES:\n{json.dumps(flagged_bodies, indent=2)}",
            f"EXTRACTED FACTS:\n{json.dumps(facts, indent=2)}",
            f"RISK FLAGS:\n{json.dumps(risks, indent=2)}",
            f"CONFIRMED KG EDGES:\n{json.dumps(edges, indent=2)}",
            f"REDLINES:\n{json.dumps(redlines, indent=2)}",
            f"CITABLE IDS:\n{json.dumps(sorted(request.citable), indent=2)}",
        ]
    )
