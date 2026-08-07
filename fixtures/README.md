# Fixtures — Clause NER + Knowledge Graph (Dev 3)

Everything here is **generated**, not hand-written:

```bash
cd backend && python -m app.pipeline --publish
```

If you need a change, change the code or `chunks.sample.json` and re-run. A test
(`tests/test_pipeline.py::test_published_fixtures_match_a_fresh_run`) fails if these
files stop matching a fresh run, so hand-edits will be caught.

## `chunks.sample.json` — **proposed input contract, Dev 2 please confirm**

This is the shape I need out of Ingestion & Chunking. It is my guess, published early so
I wasn't blocked — **counter it if it's wrong** and I'll conform.

| Field | Why I need it |
|---|---|
| `chunk_id`, `document_id`, `text` | the basics |
| `clause_ref` | the join key everything downstream is anchored to |
| `char_start`, `char_end` | the `widen_context` retry strategy walks adjacency by span |
| `section_type` | `"definitions"` chunks get pulled into the retry window regardless of adjacency |
| `page`, `ocr_confidence` | §7 — OCR confidence caps every fact's confidence, so bad OCR degrades loudly instead of silently |
| `embedding` | nullable; **this stage never reads it**, so your embedding-model choice does not block me |

One open item for Dev 2: pick an embedding model so `EMBED_DIM` can be pinned. The
migration currently declares `vector(1536)`.

The document itself is a synthetic MSA built to exercise every path — a clean override, a
precedent-resolvable ambiguity, one that needs the definitions section, one that needs the
cross-referenced clause, and one that is genuinely unresolvable.

## `mock/` — three more documents to run against

`chunks.sample.json` is one document, and one document can be tuned until it looks good.
These three are written against the drafting patterns the extractor already knew, so what
they show is generalisation rather than rules retrofitted to pass:

| | confirmed / escalated | Point |
|---|---|---|
| `chunks.doc-002.json` | 1 / 2 | nothing in the document establishes a precedent, and two cross-references are drafted too loosely for any strategy — the queue-heavy case |
| `chunks.doc-003.json` | 1 / 2 | DOC-001's clauses off a bad scan. Identical drafting; only page quality differs, and it alone pushes two resolutions below threshold |
| `chunks.doc-004.json` | 3 / 0 | a clean amendment. Empty review queue — "no escalations" has to be a reachable outcome |

Regenerate them with `python scripts/make_mock_docs.py` (which also regenerates
`chunks.sample.json`, so its char spans stay consistent). Run one with:

```bash
cd backend && python -m app.pipeline --chunks ../fixtures/mock/chunks.doc-003.json
```

`--publish` refuses to run against these — it would overwrite the output fixtures below,
which are the DOC-001 contract. Use `--out DIR` instead.

## Output fixtures

| File | For | Notes |
|---|---|---|
| `facts.sample.json` | Dev 4 | extracted facts with confidence and source chunks |
| `kg_edges.confirmed.json` | Dev 4 | **the only edges the Risk Engine may read** |
| `kg_edges.pending_review.json` | Dev 1, Dev 4 | treat as absent until resolved |
| `escalation.sample.json` | Dev 1 | queue items with the full retry trace |

### `resolved_by` — read this one

Every confirmed edge records how it got confirmed:

- `direct_extraction` — unambiguous on the first pass
- `retry:kg_precedent` / `retry:widen_context` / `retry:alternate_parse` — the agent
  resolved an ambiguity itself instead of escalating
- `human` — a reviewer confirmed it

§4.2 requires human-confirmed facts never be indistinguishable from AI-generated ones.
This field is that guarantee. In the sample document 3 of 4 ambiguities are resolved by
the retry loop and only 1 reaches the queue.

### For Dev 1 specifically

`escalation.sample.json` matches the `EscalationRecord` block in `WORK-SPLIT.md`, plus an
`id` and a `target_edge_id`. **Import the model from `backend/app/schemas.py` rather than
re-declaring the enums** — a `pending` vs `pending_review` mismatch splits the queue
silently.

`trace` is what distinguishes "nobody has looked at this yet" from "the agent already
tried three things". `rounds_attempted: 0` would mean escalated without any retry;
anything higher means the strategies in `trace` were tried and failed, in order, and each
`result` is written to be readable by a human.
