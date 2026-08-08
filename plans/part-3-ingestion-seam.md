# Part 3 — Dev 2 → Dev 3 seam

**Depends on Part 1** (needs the database for the persistence step). Benefits from Part 2's F6
guard but does not require it.

This file is self-contained. A fresh session needs nothing but this file.

---

## Context

Four codebases were built in parallel against `ARCHITECTURE.md` — Dev 1 (Frontend + Review
Queue), Dev 2 (Ingestion), Dev 3 (Clause NER + KG), and the Redline Generator (also Dev 3, by
fallback). They were audited against the architecture. The core logic is sound: the shared
escalation contract in `backend/app/schemas.py` matches `WORK-SPLIT.md:31-55` exactly, all six
frontend enums match the backend, the §3.3 confirmed-edges-only rule is enforced at both the
route and store layers, the §7 OCR confidence cap is applied before the threshold gate in both
stages, and the stores are free of SQL injection.

Four real problems did surface:

1. **Two incompatible schemas** between Supabase and Dev 3's Alembic revisions (Part 1).
2. **Stage 4 produces zero redlines from real ingestion output.** Dev 2's `Chunk` has no
   `clause_ref`, so `grounding.py:129` can never resolve a flagged clause and every risk flag
   escalates instead.
3. **Three redline defects (F3/F5/F6)** (Part 2).
4. **The frontend drops `target_redline_id`** (Part 2).

This file covers problem 2.

### Decisions already made (binding — do not relitigate)

| | |
|---|---|
| **Schema** | Rebuild to Alembic (Part 1). `schema.sql` stops being the source of truth. |
| **document_id** | `text`, not `uuid`. Accepts `"DOC-001"` and Dev 2's `uuid4` strings alike. Closes the open decision at `WORK-SPLIT.md:195-197`. |
| **DB access** | The user owns `DATABASE_URL` — they paste it into `backend/.env` themselves. Never ask for, read, print, or commit the Postgres password. DDL goes through Supabase MCP, which has its own auth. |
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
- **No test in the suite touches a real database and none runs the migrations. Passing pytest
  does not prove the Supabase work is correct.**
- Secrets live in `backend/.env` (untracked). Never echo their values.

**Step 0, run first:** capture the current baseline so you can tell your own breakage from
pre-existing failures.

```bash
cd backend && pytest
cd frontend && npm run test
```

---

## The failure

Dev 2's `Chunk` dataclass (`backend/ingestion/models.py:63-74`) has no `clause_ref`. Dev 3's
`Chunk` model (`app/models.py:60`) requires it, and `redline/grounding.py:129-138` early-returns
`"flagged clause missing"` when `ctx.clause(risk.clause_ref)` is `None`. Feed real ingestion
output into the pipeline today and **every risk flag escalates and zero redlines are produced**.
Stage 4 is effectively disabled.

Correcting the earlier audit: Dev 2's chunker is otherwise sound. `ocr_confidence`, `char_start`,
`char_end` and `chunk_index` are all present and correct, chunks never cross a page boundary
(`chunker.py:1-4`), and `ocr_confidence` is set from the page. No OCR change is needed. The
`document_id` is a bare `uuid4` string, which the text decision accepts as-is.

## 3.1 — Align the `Chunk` dataclass

In `backend/ingestion/models.py`: rename `page_number` → `page`, add `clause_ref: str`
(required — order it before the defaulted fields) and `section_type: str = "clause"`.

## 3.2 — New `backend/ingestion/clause_labeler.py`

Deterministic regex, no LLM — the label has to be reproducible across runs:

```python
CLAUSE_HEADER = re.compile(r'(?m)^\s*(\d+(?:\.\d+)*)\.?\s+([A-Z][A-Za-z ,/&\-]{2,60})\.?\s*$')
DEFINED_TERM  = re.compile(r'"([^"]+)"\s+means')
```

Call it from `chunk_document` with document-scoped carry-forward, so a chunk with no header
inherits the last clause seen. **Do not reuse `retrieval.py`'s `SECTION_REF`** — it deliberately
over-matches inline cross-references, which is right for retrieval and wrong for labelling.
`"definitions"` is the only `section_type` value any downstream code actually branches on
(`retrieval.py`), so that plus the `"clause"` default is sufficient.

## 3.3 — Persist to Postgres

`backend/ingestion/metadata_db.py` writes SQLite (`:42`, `:49`) with raw DDL and no SQLAlchemy.
**Keep it** — it is Dev 2's private pipeline-state store and tracks fields (`status`,
`storage_path`) that Dev 3's `documents` table does not have.

Add `backend/ingestion/postgres_sync.py::persist_document()` that writes `documents` and `chunks`
via `session.merge()` with explicit field selection, mirroring the existing
`app/pipeline.py::_persist` and `app/redline/store.py::save_redlines`. One call site, after
`ingestion_pipeline.py:85`.

## 3.4 — Embedding width

`embedding_service.py:12` sets `EMBEDDING_DIM = 384` but `RealEmbeddingService.model_name` is
`"text-embedding-3-small"`, which is natively 1536 — matching `config.py:24` and the
`Vector(settings.embed_dim)` column. Bump the constant to 1536. Do not change the migration or
`settings.embed_dim`. Nothing downstream reads embeddings numerically, so this is
forward-compatibility only.

## 3.5 — Packaging and the API collision

Seven files in `backend/ingestion/` import `backend.ingestion.X` (repo root), while
`backend/app/` and `backend/tests/` import bare `app.X` (backend root). These are not
simultaneously satisfiable, and the cross-package import in 3.3 needs it resolved. Rewrite the
seven to `ingestion.X`, add `backend/ingestion/__init__.py`, and change `pyproject.toml` to
`include = ["app*", "ingestion*"]`.

Separately: `backend/ingestion/api.py:37` hardcodes port 8000, which is the port
`frontend/README.md:9` says Dev 3's app owns — the two FastAPI apps collide. Convert ingestion to
an `APIRouter` and mount it in `app/api.py` under `prefix="/ingest"`. While there, fix
`/upload`, which calls `UniversalOCREngine` directly and bypasses chunking, embedding and
persistence entirely — an uploaded document never reaches Dev 3.

## 3.6 — Tests

In `backend/tests/` (**not** `backend/ingestion/tests/`, which pytest will not discover).
`test_ingestion_seam.py::test_dev2_output_produces_a_real_redline` is the one that matters: run
Dev 2's chunker over inline sample text, feed the chunks through `app.redline`, and assert at
least one redline. **Write the "before" assertion first** — it must fail with zero redlines,
proving the bug, before 3.1/3.2 make it pass.

Do not modify `fixtures/chunks.sample.json`. It is generated by
`backend/scripts/make_mock_docs.py`, hand-tuned to hit every retry branch, and pinned by
hardcoded assertions in the existing tests.

**Known blocker:** `backend/ingestion/sample_docs/` does not exist, so `publish_fixtures.py`
cannot be re-run and `vector_chunk_fixture.json` stays stale (33 records, 4 keys, all-zero
embeddings). Use inline text in tests; regenerating the published fixture needs real sample
documents from the user.

---

## Verify Part 3

1. `pytest` green, with `test_dev2_output_produces_a_real_redline` failing before and passing
   after.
2. **End to end against Supabase:** run ingestion on a document → `app.pipeline --persist` →
   `app.redline.pipeline --persist` → `curl localhost:8000/documents/<id>/redlines` returns a
   non-empty list. Redlines generated from real ingestion output rather than hand-authored
   fixtures is the actual acceptance criterion.
3. `curl -F file=@doc.pdf localhost:8000/ingest/upload` → chunks land in Postgres, not just OCR
   text returned.

---

## Deferred — documented only, not fixed

The user reviewed and excluded these. Record them in `WORK-SPLIT.md` so they are visible rather
than forgotten.

**Auth / RBAC (§7)** — explicitly out of scope. There is no authentication anywhere in the
system. `reviewer_id` is a free-text `<input>` at `EscalationDetail.tsx:147-150` with no token
behind it, so anyone can attribute a resolution to anyone, which undermines the §4.2 guarantee
that human-confirmed and AI-generated facts stay distinguishable. And `documents.rbac_tags` is
written but never read by any query. This is the largest known gap.

Lower-severity, deliberately left alone:

| | |
|---|---|
| F1 | `RetrievalContext` is a mutable dataclass; the confirmed-only raise is construction-time only |
| F2 | `pipeline.py:34-47` builds `unresolved_pairs` from unvalidated `pending_edges` |
| F4 | Two different diagnoses both report `budget_exhausted` |
| F7 | `assess()` runs 3× per loop iteration |
| A-2 | `types.ts` omits `grounding_chunk_ids` / `grounding_edge_ids` |
| A-4 | `listFacts` / `useFacts` are dead code |
| A-5 | `statuses` can reach `[]` |
| — | `VITE_API_BASE` is hardcoded to `/api` with no env override |
| — | `EscalationReason.BUDGET_EXHAUSTED` is never emitted by the `clause_ner` path |
