# Clause NER + Knowledge Graph, and the Redline Generator — Dev 3

Stage 2 of the pipeline (`ARCHITECTURE.md` §3.2). Reads chunks, writes structured facts
and typed KG edges, and runs a **bounded retry loop before escalating an ambiguous edge
to a human**.

Also here: **Stage 4, the Redline Generator** (`ARCHITECTURE.md` 3.4 and 4.1), taken
by fallback priority per `WORK-SPLIT.md`. See [the redline loop](#the-redline-loop).

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"

# For real uploads (`/ingest/upload`), also install the `ocr` extra:
# .venv/Scripts/pip install -e ".[ocr]"

# In-memory, no database needed. Prints what was resolved and by which strategy.
python -m app.pipeline

# Three more documents to run against: no-precedent, bad-scan, clean-amendment.
# See fixtures/README.md for what each one is meant to show.
python scripts/make_mock_docs.py
python -m app.pipeline --chunks ../fixtures/mock/chunks.doc-003.json

# Stage 4: redlines for the flagged clauses. Also in-memory, also no database.
python -m app.redline.pipeline

# Regenerate the fixtures other devs build against (DOC-001 only)
python -m app.pipeline --publish
python -m app.redline.pipeline --publish

# With a database (Supabase)
# Put DATABASE_URL in backend/.env first — copy .env.example and fill it in. Use the
# Session pooler string (port 5432), not the transaction pooler on 6543: Alembic issues
# prepared statements the transaction pooler cannot handle. Keep the `+psycopg` suffix.
alembic upgrade head
python -m app.pipeline --persist
python -m app.redline.pipeline --persist
uvicorn app.api:app --reload

pytest
```

`docker-compose.yml` is still here as an offline fallback — `docker compose up -d db`,
then point `DATABASE_URL` at localhost and the same `alembic upgrade head` works. Supabase
is the shared database; docker is for working without a network.

The schema lives in `alembic/` and nowhere else. The repo-root `schema.sql` is superseded
and must not be run.

## What's here

| Path | |
|---|---|
| `app/schemas.py` | **shared contract** — `EscalationRecord`, `ReviewStatus`. Dev 1 and the Redline Generator import from here |
| `app/models.py`, `alembic/` | initial Metadata DB schema for the whole pipeline; extend with new revisions |
| `app/extraction/` | `LLMProvider` boundary — `MockProvider` by default, `CodexProvider` at `LLM_MODE=live` |
| `app/ambiguity/` | the bounded retry loop and its three strategies |
| `app/escalation.py` | writes queue items when the budget runs out |
| `app/redline/` | the Stage 4 active retrieval loop, its own provider, store and routes |
| `app/api.py` | two read routes; the KG route defaults to confirmed-only |
| `app/orchestration.py`, `app/playbook_store.py` | chains Clause NER -> Risk -> Redline against real chunks — see below |

## Real uploads now run the whole pipeline

Until now, each stage only ever ran by hand from its own CLI (`python -m app.pipeline`,
`app.risk.pipeline`, `app.redline.pipeline`), against a JSON fixture. Chunks written to
the Vector DB by `/ingest/upload` — real embeddings and all — were never read back by
anything (`ingestion/embedding_service.py` said as much). `app.orchestration` is that
missing read: `ingestion.api.upload_document` now calls
`process_document(session, document_id, chunks)` right after chunks are persisted, which
runs Clause NER, then the Risk Engine, then the Redline Generator against that document's
real data and commits each stage's output before the next one runs.

It's best-effort — a stage failure (no reachable database, an empty playbook, a bad
extraction call) is logged and stops the chain there, but never undoes the ingestion or
fails the upload response. The response gains a `pipeline` field with each stage's output
count, all zero if the chain didn't get that far.

The Risk Engine needs at least one active row in `playbook_rules`
(`app.playbook_store.load_active_playbook` reads it, one query, into both the shape
`run_risk_assessment` needs and the shape the Redline Generator needs) — an empty table
means every upload's chain stops after Clause NER, which is exactly what an empty
playbook should do rather than silently fabricate a risk assessment. Seed it (or point a
migration/fixture-load at it) before expecting risk flags or redlines from a real upload.

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

## The ambiguity module interface

If you're adding a fourth strategy, this is the shape to match.

The entry point is `resolve(candidate: CandidateEdge, ctx: ResolutionContext, budget:
int | None = None) -> Resolution` (`app/ambiguity/resolver.py`). `budget` defaults to
`settings.retry_budget`.

- **`CandidateEdge`** (`app/schemas.py`) — an edge the extractor could not commit to a
  single type. Fields: `edge_id`, `document_id`, `src_clause_ref`, `dst_clause_ref`,
  `candidate_types: list[EdgeType]`, `confidence`, `evidence_chunk_ids`, `pattern_key`,
  plus the computed `is_ambiguous` property (`len(candidate_types) > 1`).
- **`ResolutionContext`** (`app/ambiguity/strategies.py`) — everything a strategy is
  allowed to look at: `chunks`, `provider`, `confirmed_edges`. Deliberately plain data
  with no database access, so the retry loop is unit-testable without one.
- **The strategy contract** — a strategy is a plain function `(candidate: CandidateEdge,
  ctx: ResolutionContext) -> StrategyOutcome`, registered as a `(name, fn)` tuple in
  `STRATEGIES` (`strategies.py`). Order matters: cheapest and most auditable first. The
  three that exist today are `kg_precedent`, `widen_context` and `alternate_parse`.
- **`StrategyOutcome`** — `resolved`, `summary` (written verbatim into the retry trace,
  so it has to read as an explanation to a human, not a debug string), `edge_type`,
  `confidence`.
- **`Resolution`** — what `resolve()` returns to the pipeline: `resolved`, `trace:
  list[TraceRound]`, `edge_type`, `confidence`, `resolved_by`, and the computed
  `rounds_attempted` (`len(trace)`).

## The redline loop

`ARCHITECTURE.md` 3.4 names this the highest hallucination-risk component in the system,
so 4.1 specifies it as an **agentic active retrieval loop** rather than a top-k fetch and
a prompt: query, evaluate what came back, refine, and only then write.

    risk flag -> seed -> [assess gaps -> targeted query] x N -> redline | escalation

The evaluate step is `redline/grounding.py::assess`. It does not return a score, it
returns the *specific* open gaps, each already expressed as the one query that would
close it, so the next round is targeted rather than a re-run. `assess() == []` is the
definition of sufficient grounding. The gaps, in the priority they are issued:

1. a clause the flagged clause cross-references that has not been read
2. a defined term the retrieved text (or the rule's `standard_language`) relies on
3. a confirmed KG edge bearing on the flagged clause whose far end has not been read
4. a clause pair the Knowledge Graph has not settled - the gap retrieval **cannot** close

The loop is seeded with the flagged clause and the flag's `triggering_fact_ids`. Those are
not a search; the Risk Engine handed them over.

### Three exits, and the strict one

| Exit | Result |
|---|---|
| grounded, confidence >= `REDLINE_CONFIDENCE` | redline at `confirmed`, served |
| grounded, below the threshold | redline at `pending_review` **plus** a `low_confidence` escalation carrying `target_redline_id`. Not served until a human approves it |
| budget exhausted, still not grounded | a `budget_exhausted` escalation and **no redline row at all** |

The third one is 4.1 read literally: do not write an unconfirmed redline. `list_redlines`
defaults to confirmed anyway, so a held row would not be served either - but "we never
wrote it" is a stronger guarantee than "we wrote it and filtered it".

Confidence is capped by the minimum `ocr_confidence` of every chunk read, the same 7 cap
`clause_ner.extract` applies. A rewrite is no more trustworthy than the worst page it was
read off, and the cap goes on *before* the threshold gate, so a bad scan drops a redline
out of the served set instead of being confidently wrong.

Retrieval reads **confirmed KG edges only**. That is enforced in
`RetrievalContext.__post_init__`, which raises rather than filtering, so 3.3 holds for
every query the loop can make instead of for the ones anyone remembered to check. An
unsettled pair is visible to the loop as *refs only* - it learns that the graph has not
decided, never what the unconfirmed edge claimed.

Every round is logged whether it closed a gap or not. That log is the **retrieval trace**,
and it is the same `TraceRound` the retry loop emits, so Dev 1 renders one component for
both queues.

Tunables, both listed as open decisions in 8 and both needing real document volume to
calibrate:

| | default | |
|---|---|---|
| `RETRIEVAL_BUDGET` | 4 | rounds before the loop gives up and escalates |
| `REDLINE_CONFIDENCE` | 0.75 | below this a redline is held for approval, not served |

Setting `RETRIEVAL_BUDGET=0` escalates everything, which is 7's queue-everything failure
mode; the sample document is built so all four outcomes are reachable at the default.

### Routes

| | |
|---|---|
| `GET /documents/{id}/redlines` | **confirmed by default**; `?status=pending_review` to see held ones |
| `GET /redlines/{redline_id}` | one redline, any status - the Review Queue links here |

Resolving a redline escalation writes back to the redline row, exactly as resolving an
edge escalation writes back to the edge. Without that write, approval in the queue would
change nothing anyone downstream can see.
