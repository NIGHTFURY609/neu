"""Initial Metadata DB schema for the whole pipeline.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.config import settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("document_id", sa.String(), primary_key=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("rbac_tags", JSONB(), server_default="{}"),
    )

    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.document_id"), index=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=False),
        sa.Column("clause_ref", sa.String(), index=True),
        sa.Column("section_type", sa.String(), server_default="clause"),
        # Width is config-driven: Dev 2 has not pinned an embedding model yet.
        sa.Column("embedding", Vector(settings.embed_dim), nullable=True),
    )

    op.create_table(
        "facts",
        sa.Column("fact_id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.document_id"), index=True),
        sa.Column("clause_ref", sa.String(), index=True),
        sa.Column("fact_type", sa.String(), index=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_chunk_ids", ARRAY(sa.String()), server_default="{}"),
        sa.Column("provenance", sa.String(), server_default="direct_extraction"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "kg_edges",
        sa.Column("edge_id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.document_id"), index=True),
        sa.Column("src_clause_ref", sa.String(), index=True),
        sa.Column("dst_clause_ref", sa.String(), index=True),
        sa.Column("edge_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, index=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_chunk_ids", ARRAY(sa.String()), server_default="{}"),
        sa.Column("pattern_key", sa.String(), nullable=True, index=True),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # The Risk Engine's hot path: confirmed edges for one document.
    op.create_index("ix_kg_edges_doc_status", "kg_edges", ["document_id", "status"])

    op.create_table(
        "escalations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, index=True),
        sa.Column("source", sa.String(), nullable=False, index=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.document_id"), index=True),
        sa.Column("clause_ref", sa.String(), nullable=False),
        sa.Column("rounds_attempted", sa.Integer(), server_default="0"),
        sa.Column("trace", JSONB(), server_default="[]"),
        sa.Column("reviewer_id", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_edge_id", sa.String(), sa.ForeignKey("kg_edges.edge_id"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("escalations")
    op.drop_index("ix_kg_edges_doc_status", table_name="kg_edges")
    op.drop_table("kg_edges")
    op.drop_table("facts")
    op.drop_table("chunks")
    op.drop_table("documents")
