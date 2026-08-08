-- ============================================================================
-- SUPERSEDED — NOT THE SCHEMA. DO NOT RUN THIS FILE.
--
-- `backend/alembic/` is the single source of truth for the database. This file is
-- kept only as the historical record of Dev 4's original design; the three tables
-- it contributed (risk_flags, playbook_rules, page_ocr_confidence) now live in
-- `backend/alembic/versions/0003_risk_engine_tables.py`.
--
-- It also disagrees with the schema on the one point that mattered: `document_id`
-- is **text**, not uuid. The note below arguing for uuid is stale — it was written
-- against a set of live tables that have since been dropped. Every Dev 2/3 model and
-- fixture identifies a document by text, so text is what Alembic creates.
--
-- To build the database:  cd backend && alembic upgrade head
-- ============================================================================
--
-- Dev 4 (Risk & Rules Engine) — target Supabase/Postgres schema.
-- Not wired up yet — kim.py + tests still run against fixtures/sample_playbook.json.
-- This is the shape the JSON-file loader will be swapped for once we connect for real.
-- Column/table names are placeholders — rename as needed, just keep the shapes.
--
-- DEPENDS ON TABLES DEV 2 ALREADY CREATED IN SUPABASE (do not redefine, only reference):
--   public.documents(id uuid, ...)                — every document_id below is a real FK into this
--   public.chunks(id uuid, document_id uuid, ...)  — not read by Dev 4, listed for awareness only
--   public.page_ocr_confidence(...)                — not read by Dev 4
-- Dev 2: if `documents.id` ever changes type or gets renamed, the FKs in facts/kg_edges/
-- risk_flags below break — ping Dev 4 before doing that.
-- (Earlier draft of this file had document_id as text, based on a "DOC-001"-style example
-- in an illustrative payload, not the real table — corrected to uuid to match the live schema.)

-- Owned by Dev 3 (writes). Dev 4: read-only.
create table facts (
    id                  uuid primary key default gen_random_uuid(),
    document_id         uuid not null references public.documents(id),
    clause_ref          text not null,              -- e.g. "Section 4.1", for traceability
    clause_pattern      text not null,              -- matches playbook_rules.clause_pattern, e.g. "liability_cap"
    field               text not null,              -- matches a Condition.field, e.g. "amount"
    value                jsonb,                      -- native JSON scalar/list; null = fact explicitly absent
    value_type          text not null,              -- "number" | "string" | "boolean" | "list" — explicit, not inferred from JSON
    extraction_confidence numeric,
    created_at          timestamptz not null default now()
);

-- Owned by Dev 4 (reads + writes). DB-backed replacement for the JSON playbook file.
-- Multiple rows can share rule_id (version history) but only one is_active at a time —
-- mirrors kim.py's "one active entry per rule_id" invariant, just without deleting history.
create table playbook_rules (
    id                  uuid primary key default gen_random_uuid(),
    rule_id             text not null,
    version             int not null,
    is_active           boolean not null default true,
    clause_pattern      text not null,
    conditions          jsonb not null,             -- [{"field":..., "operator":..., "value":...}, ...] — ANDed
    severity            text not null check (severity in ('low','medium','high','critical')),
    rationale           text not null,
    allowed_overrides   text[] not null default '{}',   -- must match kg_edges.relation_type values
    standard_language   text,
    created_at          timestamptz not null default now(),
    unique (rule_id, version)
);
create unique index one_active_version_per_rule on playbook_rules (rule_id) where is_active;

-- Owned by Dev 3 (writes). Dev 4: read-only, confirmed-only.
create table kg_edges (
    id                  uuid primary key default gen_random_uuid(),
    document_id         uuid not null references public.documents(id),
    source_clause_ref   text not null,
    target_clause_ref   text not null,
    relation_type       text not null,              -- "OVERRIDES" | "DEPENDS_ON" | "WAIVES" | "MODIFIES"
    status              text not null check (status in ('pending_review','confirmed','rejected')),
    created_at          timestamptz not null default now()
);

-- Owned by Dev 4 (reads + writes). The audit trail + the fixture Redline Generator consumes.
create table risk_flags (
    id                  uuid primary key default gen_random_uuid(),
    document_id         uuid not null references public.documents(id),
    clause_ref          text not null,
    rule_id             text not null,
    rule_version        int not null,               -- snapshot at evaluation time, even if the rule later changes
    severity            text not null,
    status              text not null check (status in ('flagged','suppressed')),
    suppressing_edge_id uuid references kg_edges(id),  -- set only when status = 'suppressed'
    triggering_fact_ids uuid[] not null,             -- facts(id) that matched the rule's conditions
    evaluated_at        timestamptz not null default now()
);
