"""Redline Generator: the redlines table and the escalation link back to it.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "redlines",
        sa.Column("redline_id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.document_id"), index=True),
        # No FK: risk_flags is Dev 4's table and does not exist yet.
        sa.Column("risk_id", sa.String(), nullable=False, index=True),
        sa.Column("clause_ref", sa.String(), index=True),
        sa.Column("status", sa.String(), nullable=False, index=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("suggested_text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("rounds_attempted", sa.Integer(), server_default="0"),
        sa.Column("trace", JSONB(), server_default="[]"),
        sa.Column("grounding_chunk_ids", ARRAY(sa.String()), server_default="{}"),
        sa.Column("grounding_edge_ids", ARRAY(sa.String()), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Mirrors ix_kg_edges_doc_status — the serving path is confirmed redlines for one
    # document, and it is the read Dev 1's dashboard makes on every page load.
    op.create_index("ix_redlines_doc_status", "redlines", ["document_id", "status"])

    op.add_column(
        "escalations",
        sa.Column("target_redline_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_escalations_target_redline_id",
        "escalations",
        "redlines",
        ["target_redline_id"],
        ["redline_id"],
    )
    op.create_index("ix_escalations_target_redline_id", "escalations", ["target_redline_id"])


def downgrade() -> None:
    op.drop_index("ix_escalations_target_redline_id", table_name="escalations")
    op.drop_constraint("fk_escalations_target_redline_id", "escalations", type_="foreignkey")
    op.drop_column("escalations", "target_redline_id")
    op.drop_index("ix_redlines_doc_status", table_name="redlines")
    op.drop_table("redlines")
