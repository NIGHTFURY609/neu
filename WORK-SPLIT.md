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
| *(by fallback)* | Redline Generator | §3.4, §4.1 | Dev 2/3/4's fixtures | Redline output fixture for Dev 1 |

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

## Redline Generator — assigned by fallback priority

**Priority order: Dev 3 → Dev 4 → Dev 2.** Whoever clears their primary scope first takes
it.

- **Agentic active retrieval loop** (§4.1) against Vector DB + KG, capped rounds — query,
  evaluate whether grounding is sufficient, refine and re-query on the specific gap
- **Escalates to the Review Queue** on exhausted budget or low confidence. Never writes an
  unconfirmed redline.
- **Logs every round to the audit trail** — the retrieval trace is what keeps a
  non-deterministic retrieval process explainable after the fact
- **Mocks needed**: Dev 2's, Dev 3's, and Dev 4's fixtures
- **Must publish**: redline output fixture for Dev 1

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
