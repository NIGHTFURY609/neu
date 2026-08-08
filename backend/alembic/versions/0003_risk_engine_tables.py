"""Risk Engine tables (Dev 4), re-created under Alembic.

Revision ID: 0003
Revises: 0002

These three tables previously lived only in the hand-written `schema.sql`. They are
re-created here in their observed shape with one deliberate change: `document_id` is
`String`, not `uuid`. Every Dev 2/3 model and fixture identifies a document by text
(`"DOC-001"`, and Dev 2's `uuid4` strings, which are text too), so `uuid` was the side
of the collision that had to give. `schema.sql` is no longer authoritative.

`risk_flags.id` is `String` for the same reason: `redline/generator.py:80-81` derives a
redline id with `f"RL-{risk.id.removeprefix('RISK-')}"`, which is a string operation on
the flag id. `playbook_rules.id` and `page_ocr_confidence.id` stay `uuid` — nothing
references either by string.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playbook_rules",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("clause_pattern", sa.String(), nullable=False),
        sa.Column("conditions", JSONB(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("allowed_overrides", ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("standard_language", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_playbook_rules_severity",
        ),
        # A flag is joined back to its rule on (rule_id, rule_version), so the pair has
        # to be unique or the join is ambiguous.
        sa.UniqueConstraint("rule_id", "version", name="uq_playbook_rules_rule_id_version"),
    )
    # At most one active version per rule. Partial, because superseded versions stay in
    # the table forever — a flag has to stay justifiable against the version that fired.
    op.create_index(
        "ix_one_active_version_per_rule",
        "playbook_rules",
        ["rule_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "risk_flags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "document_id", sa.String(), sa.ForeignKey("documents.document_id"), index=True
        ),
        sa.Column("clause_ref", sa.String(), nullable=False, index=True),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, index=True),
        # Nullable: only a `suppressed` flag names the confirmed edge that waived it.
        sa.Column(
            "suppressing_edge_id",
            sa.String(),
            sa.ForeignKey("kg_edges.edge_id"),
            nullable=True,
        ),
        # Text, not uuid: fact ids are text ("DOC-001-C004-F01"), see facts.fact_id.
        sa.Column("triggering_fact_ids", ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('flagged', 'suppressed')", name="ck_risk_flags_status"
        ),
    )

    op.create_table(
        "page_ocr_confidence",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "document_id", sa.String(), sa.ForeignKey("documents.document_id"), index=True
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("ocr_confidence", sa.Numeric(), nullable=False),
        sa.Column("ocr_engine", sa.Text(), nullable=True),
        # A Postgres generated column. Postgres only implements STORED, so persisted=True
        # is mandatory — persisted=False would emit VIRTUAL and fail.
        sa.Column(
            "low_confidence",
            sa.Boolean(),
            sa.Computed("ocr_confidence < 0.75", persisted=True),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "ocr_confidence >= 0 AND ocr_confidence <= 1",
            name="ck_page_ocr_confidence_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("page_ocr_confidence")
    # risk_flags before playbook_rules is not an FK requirement (there is no FK between
    # them), but it keeps the teardown the mirror image of the build.
    op.drop_table("risk_flags")
    op.drop_index("ix_one_active_version_per_rule", table_name="playbook_rules")
    op.drop_table("playbook_rules")
