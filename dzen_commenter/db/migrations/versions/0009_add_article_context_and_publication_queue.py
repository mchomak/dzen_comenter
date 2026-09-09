"""add publication article context and reply publication queue

Revision ID: 0009_article_publication_queue
Revises: 0008_reply_batches
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_article_publication_queue"
down_revision: Union[str, None] = "0008_reply_batches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("publications", sa.Column("article_text", sa.Text(), nullable=True))
    op.add_column(
        "publications", sa.Column("article_fetched_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "publications", sa.Column("article_content_hash", sa.Text(), nullable=True)
    )
    op.add_column(
        "publications", sa.Column("article_context_status", sa.Text(), nullable=True)
    )
    op.create_table(
        "reply_publication_queue",
        sa.Column("reply_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["reply_id"], ["replies.id"]),
        sa.PrimaryKeyConstraint("reply_id"),
    )
    op.create_index(
        "ix_reply_publication_queue_ready",
        "reply_publication_queue",
        ["state", "next_attempt_at", "created_at", "reply_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reply_publication_queue_ready", table_name="reply_publication_queue"
    )
    op.drop_table("reply_publication_queue")
    op.drop_column("publications", "article_context_status")
    op.drop_column("publications", "article_content_hash")
    op.drop_column("publications", "article_fetched_at")
    op.drop_column("publications", "article_text")
