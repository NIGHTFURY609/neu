"""Contract summarization, grounded in stored rows rather than in the document text.

ARCHITECTURE.md §6 maps every explainability requirement onto a specific store field
instead of a free-text model claim. A summary is the easiest place in the whole system to
break that rule, so this package does three things to hold the line:

  - the prompt is built from facts, risk flags, confirmed edges and redlines, not from
    the raw document. Full clause bodies go in only for clauses carrying a flagged risk.
  - every statement is a `Claim` requiring at least one citation into those rows.
  - `validate.enforce_citations` drops claims whose citations do not resolve, *after*
    generation and before anything is stored or served. It is a lookup, not a prompt
    instruction — a model cannot talk its way past it.
"""
