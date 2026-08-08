"""Negotiation fallback ladders.

The redline generator answers "what should this clause say?". A negotiator also needs
"and how far can I move?" — which is a different question, and one the playbook already
contains the answer to.

Every tier is derived from three stored things: the rule's `standard_language`, its
`allowed_overrides`, and the counterparty's original text. No LLM call is involved, which
means this ships in `LLM_MODE=mock` and is still honest — each rung traces to a versioned
playbook rule rather than to a model's opinion about what a reasonable concession is.
"""
