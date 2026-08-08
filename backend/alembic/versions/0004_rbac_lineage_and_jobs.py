"""RBAC tag shape, doc_kind + version lineage, processing job state, rule jurisdiction.

Revision ID: 0004
Revises: 0003

Four changes that all land on `documents`, plus one column on `playbook_rules`.

`document_id` stays `text` (settled in 0003 and in WORK-SPLIT.md), so the self-FK on
`parent_document_id` is text-to-text.

The `rbac_tags` backfill is the only irreversible-feeling part, and it is reversible: two
writers disagreed about the column's shape — `ingestion/postgres_sync.py` wrote
`{"legal-team": true}` while `app/pipeline.py` passed fixture dicts like
`{"confidentiality": "internal"}` straight through — and nothing ever read it back, which
is how the disagreement survived. Both collapse to one array shape here, keeping a
non-boolean value as a `"key:value"` tag so no meaning is lost. This matches
`app.auth.tags.normalize_tags` exactly; if you change one, change the other.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------- documents: classification + lineage
    op.add_column(
        "documents", sa.Column("doc_kind", sa.String(), nullable=False, server_default="contract")
    )
    op.add_column("documents", sa.Column("title", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("jurisdiction", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("contract_family", sa.String(), nullable=True))
    op.add_column(
        "documents", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("documents", sa.Column("parent_document_id", sa.String(), nullable=True))

    op.create_foreign_key(
        "fk_documents_parent_document_id",
        "documents",
        "documents",
        ["parent_document_id"],
        ["document_id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_documents_doc_kind", "documents", "doc_kind IN ('contract', 'regulation')"
    )
    op.create_check_constraint("ck_documents_version_positive", "documents", "version >= 1")
    op.create_check_constraint(
        "ck_documents_not_own_parent",
        "documents",
        "parent_document_id IS NULL OR parent_document_id <> document_id",
    )
    op.create_index("ix_documents_contract_family", "documents", ["contract_family"])
    op.create_index("ix_documents_doc_kind", "documents", ["doc_kind"])
    op.create_index("ix_documents_parent_document_id", "documents", ["parent_document_id"])

    # Backfill BEFORE the unique constraint: every pre-existing row is v1 of its own family.
    op.execute("UPDATE documents SET contract_family = document_id WHERE contract_family IS NULL")
    op.create_unique_constraint(
        "uq_documents_family_version", "documents", ["contract_family", "version"]
    )

    # ---------------------------------------------------- documents: processing job state
    op.add_column(
        "documents",
        sa.Column("processing_status", sa.String(), nullable=False, server_default="pending"),
    )
    op.add_column("documents", sa.Column("processing_stage", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("processing_error", sa.Text(), nullable=True))
    op.add_column(
        "documents", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("processing_finished_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("processing_summary", JSONB(), nullable=False, server_default="{}"),
    )
    op.create_check_constraint(
        "ck_documents_processing_status",
        "documents",
        "processing_status IN ('pending', 'queued', 'running', 'succeeded', 'failed')",
    )
    op.create_index("ix_documents_processing_status", "documents", ["processing_status"])
    # Anything already in the table ran through the inline chain in `ingestion/api.py`,
    # so claiming 'pending' would invite a pointless re-run of every existing document.
    op.execute("UPDATE documents SET processing_status = 'succeeded'")

    # -------------------------------------------------------- rbac_tags: object -> array
    op.execute(
        """
        UPDATE documents d
        SET rbac_tags = COALESCE((
            SELECT jsonb_agg(
                CASE WHEN jsonb_typeof(e.value) = 'boolean'
                     THEN to_jsonb(e.key)
                     ELSE to_jsonb(e.key || ':' || (e.value #>> '{}'))
                END)
            FROM jsonb_each(d.rbac_tags) AS e
        ), '[]'::jsonb)
        WHERE d.rbac_tags IS NOT NULL AND jsonb_typeof(d.rbac_tags) = 'object'
        """
    )
    # Under deny-by-default an untagged document is unreachable, not public. Legacy rows
    # get the default compartment rather than being orphaned out of the demo.
    op.execute(
        """UPDATE documents SET rbac_tags = '["legal-team"]'::jsonb
           WHERE rbac_tags IS NULL OR rbac_tags = '[]'::jsonb"""
    )
    op.alter_column(
        "documents", "rbac_tags", nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    op.create_check_constraint(
        "ck_documents_rbac_tags_is_array", "documents", "jsonb_typeof(rbac_tags) = 'array'"
    )
    # Default jsonb_ops, NOT jsonb_path_ops: jsonb_path_ops does not support `?|`, which
    # is the operator JSONB.Comparator.has_any emits for the tag-overlap filter. Choosing
    # the wrong opclass here leaves the query correct but the index silently unused.
    op.create_index("ix_documents_rbac_tags", "documents", ["rbac_tags"], postgresql_using="gin")

    # ------------------------------------------------------ playbook_rules: jurisdiction
    # NULL = applies everywhere, so existing rules need no backfill decision.
    op.add_column("playbook_rules", sa.Column("jurisdiction", sa.String(), nullable=True))
    op.create_index("ix_playbook_rules_jurisdiction", "playbook_rules", ["jurisdiction"])


def downgrade() -> None:
    op.drop_index("ix_playbook_rules_jurisdiction", table_name="playbook_rules")
    op.drop_column("playbook_rules", "jurisdiction")

    op.drop_index("ix_documents_rbac_tags", table_name="documents")
    op.drop_constraint("ck_documents_rbac_tags_is_array", "documents", type_="check")
    op.execute(
        """
        UPDATE documents d
        SET rbac_tags = COALESCE((
            SELECT jsonb_object_agg(t #>> '{}', 'true'::jsonb)
            FROM jsonb_array_elements(d.rbac_tags) AS t
        ), '{}'::jsonb)
        WHERE jsonb_typeof(d.rbac_tags) = 'array'
        """
    )
    op.alter_column("documents", "rbac_tags", server_default=sa.text("'{}'::jsonb"))

    op.drop_index("ix_documents_processing_status", table_name="documents")
    op.drop_constraint("ck_documents_processing_status", "documents", type_="check")
    for column in (
        "processing_summary",
        "processing_finished_at",
        "processing_started_at",
        "processing_error",
        "processing_stage",
        "processing_status",
    ):
        op.drop_column("documents", column)

    op.drop_constraint("uq_documents_family_version", "documents", type_="unique")
    op.drop_index("ix_documents_parent_document_id", table_name="documents")
    op.drop_index("ix_documents_doc_kind", table_name="documents")
    op.drop_index("ix_documents_contract_family", table_name="documents")
    op.drop_constraint("ck_documents_not_own_parent", "documents", type_="check")
    op.drop_constraint("ck_documents_version_positive", "documents", type_="check")
    op.drop_constraint("ck_documents_doc_kind", "documents", type_="check")
    op.drop_constraint("fk_documents_parent_document_id", "documents", type_="foreignkey")
    for column in (
        "parent_document_id",
        "version",
        "contract_family",
        "jurisdiction",
        "title",
        "doc_kind",
    ):
        op.drop_column("documents", column)
