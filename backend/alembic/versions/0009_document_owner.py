"""Document ownership.

Revision ID: 0009
Revises: 0008

`rbac_tags` was the whole authorization model — a document is visible when the
principal's tags overlap the document's. Phase 1 of restoring RBAC replaces that with
per-user ownership: a document is visible only to the caller who uploaded it. This
column carries that. It is nullable, and left unbackfilled on purpose — every row that
predates this revision has no verified uploader, so it becomes unreachable rather than
guessed at.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("owner_id", sa.String(), nullable=True))
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_column("documents", "owner_id")
