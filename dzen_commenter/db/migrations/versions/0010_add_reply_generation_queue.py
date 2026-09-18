"""add durable single-comment generation queue

Revision ID: 0010_reply_generation_queue
Revises: 0009_article_publication_queue
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_reply_generation_queue"
down_revision: Union[str, None] = "0009_article_publication_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reply_generation_queue",
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claim_token", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"]),
        sa.PrimaryKeyConstraint("comment_id"),
    )
    op.create_index(
        "ix_reply_generation_queue_ready",
        "reply_generation_queue",
        ["state", "next_attempt_at", "created_at", "comment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reply_generation_queue_ready", table_name="reply_generation_queue"
    )
    op.drop_table("reply_generation_queue")
