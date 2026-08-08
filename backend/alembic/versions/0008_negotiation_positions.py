"""Negotiation fallback ladders.

Revision ID: 0008
Revises: 0007

A child table rather than extra columns on `redlines`, for three reasons: `Redline` is
pinned in WORK-SPLIT.md and mirrored in `schemas.py`, `models.py` and the frontend's
`types.ts`, so widening it is a four-file breaking change; a ladder is 1..N ordered rows
per redline, which is a child table by nature; and positions must be regenerable without
touching an approved redline's `status` or its audit trail.

`rank` is stored explicitly rather than derived from the tier enum. A ladder with no
acceptable tier — which happens whenever the rule permits no overrides — still has to
order correctly, and ordering by an enum's declaration order is a rule nobody can see.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "negotiation_positions",
        sa.Column("position_id", sa.String(), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "redline_id",
            sa.String(),
            sa.ForeignKey("redlines.redline_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("risk_id", sa.String(), nullable=False),
        sa.Column("clause_ref", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("suggested_text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        # What is given up relative to the tier above. One sentence, and the reason the
        # ladder is useful rather than just three alternative drafts.
        sa.Column("concession", sa.Text(), nullable=False),
        sa.Column("residual_severity", sa.String(), nullable=False),
        # An EdgeType drawn from playbook_rules.allowed_overrides, when this tier is
        # justified by one. NULL on tiers that are not.
        sa.Column("grounded_in_override", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "tier IN ('preferred', 'acceptable', 'walk_away')", name="ck_negotiation_tier"
        ),
        sa.CheckConstraint(
            "residual_severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_negotiation_residual_severity",
        ),
        sa.UniqueConstraint("redline_id", "tier", name="uq_negotiation_redline_tier"),
    )
    op.create_index(
        "ix_negotiation_positions_document_id", "negotiation_positions", ["document_id"]
    )
    op.create_index("ix_negotiation_positions_redline_id", "negotiation_positions", ["redline_id"])


def downgrade() -> None:
    op.drop_index("ix_negotiation_positions_redline_id", table_name="negotiation_positions")
    op.drop_index("ix_negotiation_positions_document_id", table_name="negotiation_positions")
    op.drop_table("negotiation_positions")
