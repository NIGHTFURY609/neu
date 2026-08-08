"""Full-text search index over chunks.

Revision ID: 0005
Revises: 0004

Two things here are easy to get wrong and hard to notice afterwards.

**The two-argument `to_tsvector`.** `to_tsvector(text)` picks up the session's
`default_text_search_config` and is therefore only STABLE, not IMMUTABLE, so Postgres
refuses it in a generated column. `to_tsvector('english', text)` is IMMUTABLE. Dropping
the config argument turns this migration into a confusing error at `alembic upgrade`.

**`clause_ref` is weighted A, above the clause body.** Searching "2.2" should rank the
chunks *of* clause 2.2 above the chunks that merely cite it — which is the opposite of
what body-only ranking does, since a citing clause often mentions the number more often
than the clause itself does.

Adding a STORED generated column rewrites the table. That is nothing at this scale, but
it is worth knowing before running it against anything large.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

CHUNK_TSV = (
    "setweight(to_tsvector('english', coalesce(clause_ref, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(text, '')), 'B')"
)


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column("search_tsv", TSVECTOR(), sa.Computed(CHUNK_TSV, persisted=True), nullable=True),
    )
    op.create_index("ix_chunks_search_tsv", "chunks", ["search_tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_search_tsv", table_name="chunks")
    op.drop_column("chunks", "search_tsv")
