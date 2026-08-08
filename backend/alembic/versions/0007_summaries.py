"""Cached contract summaries.

Revision ID: 0007
Revises: 0006

One row per document, keyed on the document id, so `session.merge` is the upsert — the
same idiom as every other store here.

Cached rather than regenerated per request for three reasons: in `LLM_MODE=live` this is
a paid Opus call on a view that remounts on every navigation; a summary is an audit
artifact, and the reviewer who made a decision against a particular summary needs that
exact text retrievable afterwards; and invalidation is trivially correct here because the
inputs are all rows, so a fingerprint over their ids detects staleness exactly.

History is not kept. `document_id` as the primary key means a regeneration replaces the
previous summary — if a reason appears to need the old one back, this table wants a
composite key and an `is_current` flag, not a second table.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "summaries",
        sa.Column(
            "document_id",
            sa.String(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # sha256 over the sorted ids that fed the prompt. A resolved escalation or a new
        # risk flag changes it, which is what marks the stored summary stale.
        sa.Column("input_fingerprint", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("summaries")
