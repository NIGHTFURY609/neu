# Work Split — Legal Intelligence Copilot

Companion to [`ARCHITECTURE.md`](ARCHITECTURE.md). That document says *what* we're
building; this one says *who builds which part*.

Work is split **by pipeline stage** — each dev owns one box in the §5 data-flow diagram,
end to end. Every dev is responsible for **publishing a fixture** of their stage's output
as soon as they have one, so the dev downstream can build against real shapes instead of
guesses.

---

## Ownership at a glance

| Dev | Owns | Architecture ref | Mocks needed | Must publish |
|---|---|---|---|---|
| **Dev 1** | Frontend + Human Review Queue | §4.2, §6 | Sample Metadata DB records, sample `pending_review` items | — |
| **Dev 2** | Ingestion & Chunking | §3.1 | **None** — root stage, uses real sample docs | Vector DB chunk fixture, `Document_ID` fixture |
| **Dev 3** | Clause NER + Knowledge Graph | §3.2, §4.2 | Dev 2's Vector DB fixture | Fact fixtures + KG edge fixtures (both `confirmed` and `pending_review` cases) |
| **Dev 4** | Risk & Rules Engine | §3.3 | Dev 3's fixtures, sample playbook | Risk-flagged clause fixture (the one that triggers a redline) |
| **Dev 3** *(taken by fallback)* | Redline Generator | §3.4, §4.1 | Dev 2/3/4's fixtures | Redline output fixture for Dev 1 — **published**, `fixtures/redline.sample.json` |

### The one shared field — pin this before anyone writes code

Fixture-publishing covers each stage's *own* output shape, but three different people
write the same escalation record: Dev 3, the redline owner, and Dev 1 (on resolve). A
mismatch here is **silent** — if Dev 3 writes `pending_review` and the redline owner writes
`pending`, the queue splits into two buckets, Dev 1's UI shows fewer items, and nothing
errors. Agree on these values now; everything else can be PR-fixed later.

**Status enum — exactly these three, lowercase, no variants:**

`pending_review` · `confirmed` · `rejected`

**Escalation record:**

```json
{
  "status": "pending_review",
  "source": "clause_ner | redline_generator",
  "reason": "ambiguous_edge | budget_exhausted | low_confidence",
  "document_id": "...",
  "clause_ref": "...",
  "rounds_attempted": 3,
  "trace": [
    { "round": 1, "attempt": "...", "result": "...", "resolved": false }
  ],
  "reviewer_id": null,
  "resolved_at": null
}
```

`trace` is the same array for both sources — Dev 3 fills `attempt` with the retry strategy
it tried, the redline owner fills it with the query it issued. Dev 1 renders one component
for both. `reviewer_id` and `resolved_at` stay `null` until resolution.

First person to touch this owns it; everyone else conforms. Any change updates this block
in the same PR.

---

## Dev 1 — Frontend + Human Review Queue

- **Compliance / Risk Dashboards UI**
- **Review Queue UI + API**
  - `GET /review-queue`
  - `POST /review-queue/{id}/resolve` → writes `status`, `reviewer_id`, `timestamp` to
    Metadata DB
- **Consumes Metadata DB reads**: clause refs, confidence scores, risk severity, redline
  output
- **Mocks needed**: sample Metadata DB records, sample `pending_review` queue items

### Extension — surface *why* a human was needed

Not new logic, just a field to surface. Escalations now arrive in two flavors:

- **raw** — escalated immediately (old behavior)
- **after N failed retry rounds** — the agent already tried (new behavior, see Dev 3)

The queue must show which. Display the **retry trace**: what the agent tried, and how many
rounds it burned. Without this a reviewer can't distinguish *"nobody has looked at this
yet"* from *"the agent already tried and failed"* — and that distinction is what makes the
queue trustworthy rather than just a pile of work.

---

## Dev 2 — Ingestion & Chunking

Root stage. Nothing downstream exists until this runs, and it's the only dev with **zero
mock dependencies** — start immediately on real sample documents.

- **Raw Files store** — secure upload, access-controlled
- **Metadata DB writes** — `Document_ID`, upload timestamp, RBAC tags
- **Vector DB writes** — OCR → chunking → embeddings
- **Per-page OCR confidence** — §7 flags bad OCR as a silent failure that degrades every
  downstream stage with no error surfaced. Track and expose it per page.
- **Mocks needed**: none
- **Must publish**: Vector DB chunk fixture, `Document_ID` fixture

Publishing those two fixtures early is the highest-leverage thing anyone does in the first
few hours — it unblocks Dev 3, which unblocks Dev 4.

### Seam with Dev 3 — closed

`Chunk` now matches `app.schemas.Chunk` field for field. It previously had no
`clause_ref`, and Dev 3 keys everything off that: the Redline Generator resolves a
flagged clause by ref, so every risk flag escalated and Stage 4 produced **zero**
redlines from real ingestion output, with no error anywhere. `ingestion/clause_labeler.py`
assigns the ref from clause headings with document-scoped carry-forward, and chunks now
stop at clause boundaries as well as page boundaries — a chunk spanning two clauses has
no single correct ref. `tests/test_ingestion_seam.py` pins both the fix and the failure
mode.

Also settled here: ingestion is an `APIRouter` mounted at `/ingest` on the one app
(both apps defaulted to port 8000), `/ingest/upload` runs the full pipeline instead of
returning bare OCR text, `ingestion/postgres_sync.py` writes `documents` + `chunks` to
the database Dev 3 reads, and imports are bare `ingestion.X` rooted at `backend/`, the
same root `app.X` uses. `metadata_db.py` stays: it is Dev 2's private pipeline state
(`status`, `storage_path`, per-page OCR confidence), none of which Dev 3's tables hold.

---

## Dev 3 — Clause NER + Knowledge Graph

- **Reads Vector DB** → extracts structured facts → **writes Metadata DB**
  (liability caps, jurisdiction, dates, monetary values, obligations, parties, each with an
  extraction confidence)
- **Writes confirmed KG edges** — typed structural relationships
  (`OVERRIDES`, `DEPENDS_ON`, `WAIVES`, `MODIFIES`)
- **Ambiguity handling** — bounded retry loop, then escalate (below)
- **Mocks needed**: Dev 2's Vector DB fixture
- **Must publish**: fact fixtures + KG edge fixtures, covering **both** the `confirmed` and
  the `pending_review` case
- **Redline fallback priority**: 1st

### The genuinely new logic — bounded retry before escalation

§3.2 currently says: ambiguous edge → write `status=pending_review` → escalate. That sends
a human every ambiguity, including ones the agent could have resolved itself. §7 names
exactly this as a risk — over-escalation turns the system into a queue-everything machine
that defeats its own purpose.

So: **before** writing `pending_review`, run a capped retry loop.

Each round tries a different resolution strategy:

1. **Re-query the KG for similar already-confirmed edge patterns** — has this same
   relationship shape been resolved elsewhere in the document? If so, apply that precedent.
2. **Pull wider surrounding-clause context** — the disambiguating language often sits in an
   adjacent clause or a definitions section, just outside the original chunk window.
3. **Try alternate parses** — if the ambiguity is `WAIVES` vs `MODIFIES`, test each reading
   against the surrounding text and see which one is consistent.

**Escalate to the Review Queue only if still ambiguous after the budget runs out.**

Every round is logged — strategy tried, what came back, whether it resolved. That log is
the **retry trace**: it goes into the audit trail, and Dev 1 renders it in the queue. Cap
the round count (start at 3 and tune) so a single ambiguous edge can't blow out latency.

---

## Dev 4 — Risk & Rules Engine

Fully deterministic — never touches raw text or embeddings. That's what makes its output
auditable and reproducible.

- **Reads Metadata DB** — extracted facts + the compliance playbook
- **Reads KG — confirmed edges only.** `pending_review` edges are treated as absent until
  resolved.
- **Writes Metadata DB** — risk severity scores + audit trail, each keyed to the specific
  clause / fact / rule version that triggered it
- **Defines the playbook rule schema** — §8 open decision, closed here. Structured and
  versioned (`condition → clause pattern → severity`), stored as data, not prose. The
  engine can only be deterministic if the playbook itself is structured.
- **Mocks needed**: Dev 3's fixtures, sample playbook
- **Must publish**: a risk-flagged clause fixture — specifically one that triggers a redline,
  since that's what the Redline Generator needs as input
- **Redline fallback priority**: 2nd

---

## Redline Generator — taken by Dev 3

**Priority order was: Dev 3 → Dev 4 → Dev 2.** Dev 3 cleared Clause NER + KG first and
took it. Built, tested and published — see `backend/README.md`.

- **Agentic active retrieval loop** (§4.1) against Vector DB + KG, capped rounds — query,
  evaluate whether grounding is sufficient, refine and re-query on the specific gap
- **Escalates to the Review Queue** on exhausted budget or low confidence. Never writes an
  unconfirmed redline.
- **Logs every round to the audit trail** — the retrieval trace is what keeps a
  non-deterministic retrieval process explainable after the fact
- **Mocks needed**: Dev 2's, Dev 3's, and Dev 4's fixtures
- **Must publish**: redline output fixture for Dev 1

**Status — done.** `backend/app/redline/`, run with `python -m app.redline.pipeline`.
Published: `fixtures/redline.sample.json` and `fixtures/escalation.redline.json`, both
generated rather than hand-written. Routes: `GET /documents/{id}/redlines` (confirmed only
by default) and `GET /redlines/{redline_id}`. Redline escalations land in the *same* queue
Dev 3's ambiguous edges do, through the same `EscalationRecord`, with a new
`target_redline_id` so resolving one flips the redline itself.

Two things this needs from other people:

- **Dev 4** — `fixtures/risk.sample.json` and `fixtures/playbook.sample.json` are proposals
  in your `schema.sql` shape, joined on `(rule_id, rule_version)`. Counter them if they
  are wrong. The generator triggers on `status == 'flagged'` and skips `suppressed`.
- **Dev 1** — `risk.sample.json` **changed shape**: it lost `title`, `rationale` and
  `triggers_redline`. `rationale` moved to the playbook rule; `triggers_redline` is now
  `status == 'flagged'`. Redlines now carry a `status` too, and held ones are not served
  by default. Details in `fixtures/README.md`.

**Settled: `document_id` is `text`.** `schema.sql` typed it `uuid`; every Dev 2/3 model and
fixture uses text (`"DOC-001"`). Text won, because it is the only type that accepts both —
Dev 2's ids are `uuid4` *strings*, which store fine in a text column, while `"DOC-001"` can
never be a uuid. The two schemas no longer merely share a database: `schema.sql` has been
retired and `backend/alembic/` is the single source of truth. Dev 4's three tables were
re-created with text `document_id` in revision `0003_risk_engine_tables.py`.

---

## Dependency chain

```
Dev 2 ──chunk + Document_ID fixtures──> Dev 3 ──fact + KG edge fixtures──> Dev 4
                                                                            │
                                                          risk-flagged clause fixture
                                                                            ↓
                                                                  Redline Generator
                                                                            │
                                                              redline output fixture
                                                                            ↓
                                                                          Dev 1
```

Dev 1 works off hand-written sample records until real fixtures land, so nobody is idle —
but the fixtures are what make the seams line up at integration. Publish early, publish
rough, republish when real.

---

## Two escalation sources feed one queue

Both paths land in the same Review Queue, and Dev 1's UI must handle both:

| Source | Escalates when | Trace shown |
|---|---|---|
| **Dev 3** — ambiguous KG edge | Retry budget exhausted, still ambiguous | Retry trace — strategies tried, rounds burned |
| **Redline Generator** — §4.1 | Retrieval budget exhausted, or confidence below threshold | Retrieval trace — queries issued, what returned |

Resolution in both cases writes back `status` (`confirmed`/`rejected`), `reviewer_id`, and
`timestamp` — permanently and distinctly tagged, so human-confirmed facts are never
indistinguishable from AI-generated ones.

---

## Known gaps — reviewed and deliberately not fixed

Recorded so they are visible rather than forgotten. Every one of these was found, judged,
and left alone on purpose; none is a discovery waiting to happen.

### Auth / RBAC (§7) — the largest known gap

**There is no authentication anywhere in the system.** Consequences, in order of how much
they matter:

- `reviewer_id` is a free-text `<input>` (`EscalationDetail.tsx`) with no token behind it.
  Anyone can attribute a resolution to anyone, which undermines the §4.2 guarantee that
  human-confirmed and AI-generated facts stay distinguishable — the tag survives, but it
  no longer means a specific human stood behind it.
- `documents.rbac_tags` is written by ingestion and read by no query. Tagging happens;
  gating does not.
- `/ingest/upload` takes `uploader_id` and `rbac_tags` as unverified form fields.

`RawFileStore` still refuses an upload with no RBAC tag, so nothing lands untagged and
the gate can be added later without a backfill.

### Lower severity

| | |
|---|---|
| F1 | `RetrievalContext` is a mutable dataclass; the confirmed-only raise is construction-time only |
| F2 | `redline/pipeline.py` builds `unresolved_pairs` from unvalidated `pending_edges` |
| F4 | Two different diagnoses both report `budget_exhausted` |
| F7 | `assess()` runs 3× per loop iteration |
| A-2 | `types.ts` omits `grounding_chunk_ids` / `grounding_edge_ids` |
| A-4 | `listFacts` / `useFacts` are dead code |
| A-5 | `statuses` can reach `[]` |
| — | `VITE_API_BASE` is hardcoded to `/api` with no env override |
| — | `EscalationReason.BUDGET_EXHAUSTED` is never emitted by the `clause_ner` path |
| — | `ingestion/sample_docs/` does not exist, so `publish_fixtures.py` cannot be re-run and `vector_chunk_fixture.json` stays stale (33 records, all-zero embeddings). Needs real sample documents. |
