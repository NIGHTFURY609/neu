-- Dev 4 (Risk & Rules Engine) — target Supabase/Postgres schema.
-- Not wired up yet — kim.py + tests still run against fixtures/sample_playbook.json.
-- This is the shape the JSON-file loader will be swapped for once we connect for real.
--
-- facts and kg_edges are NOT defined here anymore — Dev 3 already built and migrated
-- them for real (backend/app/models.py, backend/alembic/versions/0001_initial.py).
-- Documented below as a read contract only. Do not redefine them here or they'll fight
-- Dev 3's migration for ownership of the same table names.
--
-- UNRESOLVED, TEAM DECISION NEEDED: what type is documents' primary key? Two conflicting
-- answers exist right now:
--   (a) shown to us earlier as "current Supabase tables": documents.id is uuid
--   (b) Dev 3's actual migrated model: documents.document_id is text ("DOC-001"-style),
--       and every fixture (facts, kg_edges, escalations) already uses text ids to match
-- (b) looks like the one actually in use, but this is Dev 2/Dev 3's call, not ours —
-- document_id below is text to match (b); flip it if the team resolves this the other way.

-- Owned by Dev 3 (writes, already migrated — see backend/app/models.py). Dev 4: read-only.
-- Real columns: fact_id (text pk), document_id (text), clause_ref, fact_type (matches our
-- clause_pattern), value (jsonb — a dict of named fields, NOT one scalar per row), confidence,
-- source_chunk_ids (text[]), provenance, created_at.
-- fact_type values: liability_cap | jurisdiction | date | monetary_value | obligation | party
-- KNOWN ISSUE (flagged to Dev 3, not fixed here): fixtures/facts.sample.json has
-- value.amount as "$1,000,000" — a formatted string, not a clean number. lalo.py will
-- correctly raise DataTypeError on this rather than silently miscomparing, but every
-- liability_cap rule is unevaluable until Dev 3 normalizes it.

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
    allowed_overrides   text[] not null default '{}',   -- must match Dev 3's kg_edges.edge_type values
                                                          -- (OVERRIDES | DEPENDS_ON | WAIVES | MODIFIES) — confirmed match, no drift
    standard_language   text,
    created_at          timestamptz not null default now(),
    unique (rule_id, version)
);
create unique index one_active_version_per_rule on playbook_rules (rule_id) where is_active;

-- Owned by Dev 3 (writes, already migrated). Dev 4: read-only, confirmed-only.
-- Real columns: edge_id (text pk), document_id (text), src_clause_ref, dst_clause_ref,
-- edge_type, status, confidence, evidence_chunk_ids (text[]), pattern_key, resolved_by, created_at.
-- edge_type values match kim.py's allowed_overrides exactly: OVERRIDES | DEPENDS_ON | WAIVES | MODIFIES
-- status values: pending_review | confirmed | rejected — Dev 4 reads status = 'confirmed' only.

-- Owned by Dev 4 (reads + writes). The audit trail + the fixture Redline Generator consumes.
create table risk_flags (
    id                  uuid primary key default gen_random_uuid(),
    document_id         text not null,               -- see UNRESOLVED note above
    clause_ref          text not null,
    rule_id             text not null,
    rule_version        int not null,               -- snapshot at evaluation time, even if the rule later changes
    severity            text not null,
    status              text not null check (status in ('flagged','suppressed')),
    suppressing_edge_id text,                         -- kg_edges.edge_id (text); set only when status = 'suppressed'
    triggering_fact_ids text[] not null,               -- facts.fact_id (text) that matched the rule's conditions
    evaluated_at        timestamptz not null default now()
);
