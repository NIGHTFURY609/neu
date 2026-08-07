# Clause NER + Knowledge Graph — Dev 3

Stage 2 of the pipeline (`ARCHITECTURE.md` §3.2). Reads chunks, writes structured facts
and typed KG edges, and runs a **bounded retry loop before escalating an ambiguous edge
to a human**.

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"

# In-memory, no database needed. Prints what was resolved and by which strategy.
python -m app.pipeline

# Three more documents to run against: no-precedent, bad-scan, clean-amendment.
# See fixtures/README.md for what each one is meant to show.
python scripts/make_mock_docs.py
python -m app.pipeline --chunks ../fixtures/mock/chunks.doc-003.json

# Regenerate the fixtures other devs build against (DOC-001 only)
python -m app.pipeline --publish

# With a database
docker compose up -d db
alembic upgrade head
python -m app.pipeline --persist
uvicorn app.api:app --reload

pytest
```

## What's here

| Path | |
|---|---|
| `app/schemas.py` | **shared contract** — `EscalationRecord`, `ReviewStatus`. Dev 1 and the Redline Generator import from here |
| `app/models.py`, `alembic/` | initial Metadata DB schema for the whole pipeline; extend with new revisions |
| `app/extraction/` | `LLMProvider` boundary — `MockProvider` by default, `ClaudeProvider` at `LLM_MODE=live` |
| `app/ambiguity/` | the bounded retry loop and its three strategies |
| `app/escalation.py` | writes queue items when the budget runs out |
| `app/api.py` | two read routes; the KG route defaults to confirmed-only |

## The retry loop

`ARCHITECTURE.md` §3.2 escalates every ambiguity to a human, and §7 names that
over-escalation as the risk that turns the system into a queue-everything machine. So
before writing `pending_review`, an ambiguous candidate gets up to `RETRY_BUDGET` rounds,
each looking somewhere different:

1. **`kg_precedent`** — has this drafting pattern already been settled elsewhere in this
   document? No model call; the answer names a specific confirmed edge.
2. **`widen_context`** — re-extract with the adjacent clauses and the definitions section
   in view. Span-based, not embedding-based, so it doesn't depend on Dev 2's embedding
   model.
3. **`alternate_parse`** — score the competing readings head-to-head against the clause
   on the other end of the reference.

Every round is logged whether it works or not. That log is the **retry trace** — it goes
into the audit trail and Dev 1 renders it in the queue.

Tunables live in `app/config.py`; §7 is explicit that these need real document volume to
calibrate, not a number picked up front:

| | default |
|---|---|
| `RETRY_BUDGET` | 3 |
| `RESOLVE_CONFIDENCE` | 0.75 |
| `ALTERNATE_PARSE_MARGIN` | 0.15 |

`RETRY_BUDGET=0` degrades cleanly to the original §3.2 behaviour.
