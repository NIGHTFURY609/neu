"""Regulatory provisions: the seeded statute corpus.

Revision ID: 0006
Revises: 0005

A separate table rather than `chunks` rows with `doc_kind='regulation'`, for four
reasons that all come down to a statute not being an uploaded document:

  - a `chunks` row needs a `documents` FK, which would mean inventing a fake Document for
    GDPR with a filename, an uploader and — worst — `rbac_tags`. Published law is not
    access-controlled, and `visible_documents()` is deliberately never applied here.
  - `page`, `char_start`, `ocr_confidence` are meaningless for a statute, and
    `ocr_confidence` in particular propagates into downstream confidence caps.
  - the risk-flag -> regulation join needs `rule_ids`, `clause_patterns` and `topic_tags`
    as first-class filterable arrays, not JSONB stuffed into a chunk.
  - 800-character chunking would split Art. 32 mid-sentence and destroy the citation. A
    provision is the atomic unit; it is not a chunk.

User-uploaded regulations still go through `/ingest/upload` with `doc_kind='regulation'`
and land in `documents`/`chunks`, where 0005's generated column indexes them for free.
Both paths are reachable from `/search?scope=`.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

PROVISION_TSV = (
    "setweight(to_tsvector('english', coalesce(citation, '') || ' ' || coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(summary, '')), 'B') || "
    "setweight(to_tsvector('english', coalesce(body, '')), 'C')"
)


def upgrade() -> None:
    op.create_table(
        "regulatory_provisions",
        # Stable, human-authored id ("gdpr-art-32") so re-seeding is a merge, not a
        # duplicate.
        sa.Column("provision_id", sa.String(), primary_key=True),
        sa.Column("instrument", sa.String(), nullable=False),
        sa.Column("instrument_title", sa.String(), nullable=False),
        sa.Column("citation", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("jurisdiction", sa.String(), nullable=False),
        # Verbatim statutory text.
        sa.Column("body", sa.Text(), nullable=False),
        # One authored plain sentence. Not generated — a paraphrase of law needs a human.
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("topic_tags", ARRAY(sa.String()), nullable=False, server_default="{}"),
        # Joins playbook_rules.clause_pattern.
        sa.Column("clause_patterns", ARRAY(sa.String()), nullable=False, server_default="{}"),
        # Joins playbook_rules.rule_id. Authored, which is what makes a match citable
        # rather than merely suggestive.
        sa.Column("rule_ids", ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("retrieved_at", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "search_tsv", TSVECTOR(), sa.Computed(PROVISION_TSV, persisted=True), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_reg_prov_tsv", "regulatory_provisions", ["search_tsv"], postgresql_using="gin"
    )
    op.create_index(
        "ix_reg_prov_rule_ids", "regulatory_provisions", ["rule_ids"], postgresql_using="gin"
    )
    op.create_index(
        "ix_reg_prov_clause_patterns",
        "regulatory_provisions",
        ["clause_patterns"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_reg_prov_topic_tags", "regulatory_provisions", ["topic_tags"], postgresql_using="gin"
    )
    op.create_index("ix_reg_prov_jurisdiction", "regulatory_provisions", ["jurisdiction"])


def downgrade() -> None:
    op.drop_index("ix_reg_prov_jurisdiction", table_name="regulatory_provisions")
    op.drop_index("ix_reg_prov_topic_tags", table_name="regulatory_provisions")
    op.drop_index("ix_reg_prov_clause_patterns", table_name="regulatory_provisions")
    op.drop_index("ix_reg_prov_rule_ids", table_name="regulatory_provisions")
    op.drop_index("ix_reg_prov_tsv", table_name="regulatory_provisions")
    op.drop_table("regulatory_provisions")
