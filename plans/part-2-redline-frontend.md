# Part 2 — Redline hardening + frontend wiring

**No database required.** Can run in parallel with Part 1.

This file is self-contained. A fresh session needs nothing but this file.

---

## Context

Four codebases were built in parallel against `ARCHITECTURE.md` — Dev 1 (Frontend + Review
Queue), Dev 2 (Ingestion), Dev 3 (Clause NER + KG), and the Redline Generator (also Dev 3, by
fallback). They were audited against the architecture. The core logic is sound: the shared
escalation contract in `backend/app/schemas.py` matches `WORK-SPLIT.md:31-55` exactly, all six
frontend enums match the backend, the §3.3 confirmed-edges-only rule is enforced at both the
route and store layers, the §7 OCR confidence cap is applied before the threshold gate in both
stages, and the stores are free of SQL injection. The silent queue-split failure
`WORK-SPLIT.md:26-29` warns about did not happen.

Four real problems did surface:

1. **Two incompatible schemas** between Supabase and Dev 3's Alembic revisions (Part 1).
2. **Stage 4 produces zero redlines from real ingestion output** (Part 3).
3. **Three redline defects (F3/F5/F6)** that only bite outside the mock provider.
4. **The frontend drops `target_redline_id`,** so half the review queue links nowhere.

This file covers problems 3 and 4.

### Decisions already made (binding — do not relitigate)

| | |
|---|---|
| **Schema** | Rebuild to Alembic (Part 1). `schema.sql` stops being the source of truth. |
| **document_id** | `text`, not `uuid`. Accepts `"DOC-001"` and Dev 2's `uuid4` strings alike. |
| **DB access** | The user owns `DATABASE_URL` — they paste it into `backend/.env` themselves. Never ask for, read, print, or commit the Postgres password. |
| **Scope** | Dev 2→Dev 3 seam · Redline F3/F5/F6 · Frontend A-1/A-3. Auth/RBAC (§7) was explicitly excluded — document only. |

---

## Shared context

- Repo: `N:\coding\hackathons\neu`. Python package root is `backend/` (imports are bare
  `app.X`); frontend is Vite + React + TS in `frontend/`.
- `ARCHITECTURE.md` is the source of truth for behaviour. `WORK-SPLIT.md` pins the cross-team
  contract — the status enum is exactly `pending_review` / `confirmed` / `rejected`, lowercase,
  and a mismatch fails silently.
- Backend tests: `cd backend && pytest`. Frontend: `cd frontend && npm run test`.
  `pyproject.toml` sets `testpaths = ["tests"]` — a test placed anywhere but `backend/tests/`
  is silently never run.
- No test in the suite touches a real database and none runs the migrations.
- Secrets live in `backend/.env` (untracked). Never echo their values.

**Step 0, run first:** capture the current baseline so you can tell your own breakage from
pre-existing failures.

```bash
cd backend && pytest
cd frontend && npm run test
```

---

## 2A — Redline F3/F5/F6

Apply in the order **F6 → F3 → F5**; F3 and F5 both touch `generate()`, and doing F3 first
avoids a collision.

### F6 — unguarded `KeyError` aborts the whole run

`backend/app/redline/generator.py:250` does
`rule = rules[(risk.rule_id, risk.rule_version)]`. One risk flag naming a rule version that is
not in the playbook kills generation for every remaining flag.

Use `rules.get(...)`; on miss, append to a new `rule_not_found` list on `GenerationRun`
(defined at `generator.py:54-77`) and continue. **Skip-and-log, not escalate** — an absent rule
is a data problem, not an ambiguity a human reviewer can resolve. Report it in
`app/pipeline.py::_summarise()` after line 108, matching that function's existing `print`
convention (there is no logging anywhere in `backend/app` — do not introduce it here).

### F3 — zero validation on live LLM output

`backend/app/redline/provider.py:110-119` does a bare `json.loads(raw)` and hands the dict
straight through. `generator.py` then reads three keys unguarded — `raw["confidence"]:187`,
`raw["suggested_text"]:198`, `raw["rationale"]:199`. There is no upper clamp anywhere, so a
model returning `confidence: 5.0` clears the 0.75 gate and gets served as confirmed. This is
invisible under `LLM_MODE=mock` because `MockRedlineProvider` self-clamps at `provider.py:94`.
Note `app/extraction/provider.py:191-200` has the identical defect, so there is no existing
validation pattern to copy.

Add a `RedlineDraft(BaseModel)` next to the Protocol at `provider.py:35` with
`suggested_text: str`, `rationale: str`, `confidence: float`. Validate in `generator.py`:

- **Structural failure** (missing key, unparseable JSON) → escalate, reusing the existing
  escalation tuple already built at `generator.py:158-173`.
- **Out-of-range confidence** → clamp, do not escalate. Line 187 becomes:

  ```python
  confidence = round(min(max(draft.confidence, 0.0), 1.0, state.min_ocr_confidence), 4)
  ```

  This preserves the §7 ordering — cap by OCR before the threshold gate.

### F5 — `RETRIEVAL_BUDGET=0` does the opposite of what the docs say

`backend/README.md:137` states it "escalates everything, which is §7's queue-everything failure
mode". The code does the reverse: `range(1, 0+1)` is empty at `generator.py:101`, but line 138
runs unconditionally on the seed-only state, writing a redline with `rounds_attempted=0` and
the mock provider's `BASE = 0.95` — the maximum confidence. Zero budget currently produces the
most confident possible output.

The README is right and the code is wrong. Add after line 96:

```python
if budget < 1:
    return state, [], False
```

This mirrors the retry loop, which already degrades correctly — `resolver.py:42` iterates
`STRATEGIES[:budget]`, genuinely empty at 0, pinned by
`tests/test_retry_loop.py::test_zero_budget_escalates_immediately`.

### Tests

In `backend/tests/test_redline.py`, reusing its existing `_risk()` / `_rule()` helpers:

- a flag whose `(rule_id, rule_version)` is absent (**F6**, asserting the other flags still
  generate),
- a provider returning `confidence: 5.0` and one returning a dict missing `suggested_text`
  (**F3**),
- `RETRIEVAL_BUDGET=0` producing an escalation and no redline row (**F5**).

F5 may change published output — rerun `python -m app.redline.pipeline --publish` if
`fixtures/redline.sample.json` shifts.

---

## 2B — Frontend A-1/A-3 + Dashboard error states

Paths are `frontend/src/api/` and `frontend/src/routes/` (**not** `src/pages/`).

**A-1 — `target_redline_id` is missing from the type.** `frontend/src/api/types.ts:39-51`
declares `target_edge_id` at line 41 but not `target_redline_id`, even though
`backend/app/schemas.py:63-84` returns both. `fixtures/escalation.redline.json` carries
`target_redline_id` with no `target_edge_id` at all, so redline escalations render a dead `—`
at `EscalationDetail.tsx:57`. Half the queue links nowhere.

**A-3 — `GET /redlines/{redline_id}` has zero callers.** The route exists
(`app/redline/routes.py:33-43`), `backend/README.md:145` documents it as "the Review Queue links
here", and `client.ts` never calls it.

Four files, one chain:

1. `api/types.ts` — add `target_redline_id: string | null` to `EscalationItem`.
2. `api/client.ts` — add `getRedline(id)` alongside the existing `listRedlines`. The `Redline`
   type is already imported at line 6.
3. `hooks/useReviewQueue.ts` — add `useRedline(id)` with `enabled: id !== null`, matching the
   dependent-query pattern already in that file.
4. `routes/EscalationDetail.tsx` — replace the single-target line 57 with a three-branch
   render: edge target, redline target (showing the fetched redline), or neither
   (`budget_exhausted` legitimately writes no target).

**Dashboard error states (medium).** `routes/Dashboard.tsx:51` checks `edges.isError` — the
only `isError` in the file. `risks.isError` and `redlines.isError` are never checked, and
because `undefined ?? []` collapses to `[]`, the empty states at lines 63-64 and 78-79 render
identically whether the data is empty or the API is down. A backend outage currently displays as
a clean, risk-free document. Mirror the existing `edges.isError` panel for both; `AlertIcon` is
already imported at line 5.

---

## Verify Part 2

- `cd backend && pytest` — green, with the new F3/F5/F6 tests **failing before the fix and
  passing after** (write the failing assertion first).
- `cd frontend && npm run test` — the 7 existing `it()` blocks stay green.
- Manual: load a redline-sourced escalation from `fixtures/escalation.redline.json` and confirm
  the detail view now links to the redline. Stop the backend and reload the Dashboard — it must
  show an error, not an empty document.
