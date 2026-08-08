# Part 1 — Supabase migration

**Run order:** Part 1 first (Part 3 depends on it). Part 2 is independent and can run at any
time, including concurrently with this part.

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

1. **Two incompatible schemas.** Supabase project `jiesmbcyeyazgquoqbpb` holds 7 tables from
   Dev 4's hand-written `schema.sql`; Dev 3's Alembic revisions define 6 different ones. They
   collide on `documents`, `chunks`, `facts`, `kg_edges` — and not by renames alone: the live
   tables are missing columns the architecture requires (`chunks.ocr_confidence`,
   `chunks.clause_ref`, `kg_edges.confidence`, `kg_edges.resolved_by`). `redlines` and
   `escalations` do not exist at all. All 7 live tables are empty, so a rebuild costs nothing.
2. **Stage 4 produces zero redlines from real ingestion output.** Dev 2's `Chunk` has no
   `clause_ref`, so `grounding.py:129` can never resolve a flagged clause and every risk flag
   escalates instead.
3. **Three redline defects (F3/F5/F6)** that only bite outside the mock provider.
4. **The frontend drops `target_redline_id`,** so half the review queue links nowhere.

This file covers problem 1.

### Decisions already made (binding — do not relitigate)

| | |
|---|---|
| **Schema** | Rebuild to Alembic. Drop the 7 live tables, run `alembic upgrade head`, re-add Dev 4's three tables as a new revision `0003`. `schema.sql` stops being the source of truth. |
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
- `schema.sql:12-13` contains a comment telling the reader to use `uuid`. That comment is stale
  and contradicts the decision above — ignore it.

**Step 0, run first:** capture the current baseline so you can tell your own breakage from
pre-existing failures.

```bash
cd backend && pytest
cd frontend && npm run test
```

---

## 1.1 — Get the connection string (user action)

Add to `backend/.env`. Supabase Dashboard → Project Settings → Database → Connection string →
**Session pooler** (port 5432 — the transaction pooler on 6543 does not support the prepared
statements Alembic issues, and the direct connection is IPv6-only):

```
DATABASE_URL=postgresql+psycopg://postgres.jiesmbcyeyazgquoqbpb:[PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

The `+psycopg` driver suffix is required — `backend/pyproject.toml:11` pins psycopg 3, not
psycopg2. No code change is needed to pick this up: `backend/app/config.py:12` sets
`env_prefix=""`, so `DATABASE_URL` overrides the localhost default at line 14, and both
`app/db.py:8` and `alembic/env.py:10` read `settings.database_url`. Note `alembic.ini` has no
`sqlalchemy.url` key — editing it will not repoint Alembic.

Also create `backend/.env.example` documenting every key (`DATABASE_URL`, `ANTHROPIC_API_KEY`,
`LLM_MODE`, `SUPABASE_*`) with **placeholder values only**.

## 1.2 — Drop the live tables (Supabase MCP `execute_sql`)

Children first, no `CASCADE`, so an unexpected dependency surfaces as an error rather than
silently taking something with it:

```
risk_flags → page_ocr_confidence → chunks → facts → kg_edges → playbook_rules → documents
```

Then the orphan `DROP TABLE` leaves behind:

```sql
DROP TYPE IF EXISTS public.document_status;   -- enum backing the old documents.status
```

Leave `public.set_updated_at()` in place (its trigger dies with the table; the function may be
reused). Expect the `ensure_rls` event trigger to fire on every table the migration creates and
enable RLS with no policies — that is the safe state: a SQLAlchemy connection as the `postgres`
owner bypasses RLS entirely, while PostgREST `anon` / `authenticated` are fully blocked. **Do
not disable it.**

## 1.3 — Write revision `0003_risk_engine_tables.py`

`down_revision = "0002"`. Re-creates Dev 4's three tables in their observed shape, with
`document_id` as `String` instead of `uuid`:

- **`risk_flags`** — `id` as `String` (not uuid: `redline/generator.py:80-81` derives the
  redline id via `f"RL-{risk.id.removeprefix('RISK-')}"`), `document_id` FK →
  `documents.document_id`, `clause_ref`, `rule_id`, `rule_version`, `severity`, `status` CHECK
  IN `('flagged','suppressed')`, `suppressing_edge_id` FK → `kg_edges.edge_id` nullable,
  `triggering_fact_ids` `ARRAY(String)`, `evaluated_at`.
- **`playbook_rules`** — keep `id` as uuid (nothing references it by string). `rule_id`,
  `version`, `is_active`, `clause_pattern`, `conditions` JSONB, `severity` CHECK IN
  `('low','medium','high','critical')`, `rationale`, `allowed_overrides` ARRAY,
  `standard_language` nullable, `created_at`. Unique on `(rule_id, version)` plus a partial
  index `ix_one_active_version_per_rule` on `rule_id` WHERE `is_active`.
- **`page_ocr_confidence`** — keep `id` as uuid. `document_id` FK, `page_number`,
  `ocr_confidence`, `ocr_engine` nullable, `created_at`, and `low_confidence` as
  `sa.Computed("ocr_confidence < 0.75", persisted=True)` — it is a Postgres generated column
  and Postgres only supports STORED, so `persisted=True` is mandatory.

Write a real `downgrade()` that drops all three in reverse FK order.

`op.execute("CREATE EXTENSION IF NOT EXISTS vector")` at `alembic/versions/0001_initial.py:21`
is already a no-op — pgvector 0.8.2 is installed.

## 1.4 — Apply and document

```bash
cd backend && alembic upgrade head    # 0001 → 0002 → 0003
```

Then:

- `backend/README.md:30-38` — replace the `docker compose up -d db` block with the Supabase
  `.env` instructions. Keep `docker-compose.yml` as an offline fallback and say so.
- `WORK-SPLIT.md:195-197` — replace the "Open, unresolved" paragraph with the settled decision
  (text, and why).
- `backend/app/models.py:124-126` — the comment says the `document_id` mismatch "has to be
  settled"; update it to say it was, and that `risk_flags` now exists in revision `0003`.
- `backend/app/models.py:4` says "all five tables" but six are defined. Fix while you're there.
- Retire `schema.sql` — either delete it or add a header stating Alembic is now authoritative.
  Do not leave two competing schema definitions in the repo.

---

## Verify Part 1

1. `mcp__supabase__list_tables` → exactly **9 tables**, including `redlines` and `escalations`
   (which have never existed before).
2. `mcp__supabase__list_migrations` → the Alembic ledger, previously empty.
3. `mcp__supabase__get_advisors type=security` → `rls_enabled_no_policy` INFO notices on the new
   tables are expected; nothing at ERROR.
4. **The text-`document_id` smoke test** — the thing that would have failed under uuid:

   ```bash
   cd backend
   python -m app.pipeline --persist            # writes DOC-001
   python -m app.redline.pipeline --persist    # writes RL-DOC-001-001
   uvicorn app.api:app --reload
   curl localhost:8000/documents/DOC-001/redlines
   curl localhost:8000/review-queue
   ```

   A non-empty redline list proves text ids, the FK graph, and the two new tables all work.
5. `pytest` still green (it is offline, so this only confirms no regression).

Rollback is free — every table was empty. `alembic downgrade base`, or re-run `schema.sql`.
