"""add batch reply storage

Revision ID: 0008_reply_batches
Revises: 0007_cta_candidate
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_reply_batches"
down_revision: Union[str, None] = "0007_cta_candidate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reply_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("post_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("article_context_status", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
    )
    op.create_table(
        "comment_batch_queue",
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("post_url", sa.Text(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_batch_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"]),
        sa.ForeignKeyConstraint(["claimed_batch_id"], ["reply_batches.id"]),
        sa.PrimaryKeyConstraint("comment_id"),
    )
    op.create_index(
        "ix_comment_batch_queue_ready",
        "comment_batch_queue",
        ["post_url", "state", "queued_at", "comment_id"],
    )
    op.create_table(
        "reply_batch_items",
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("item_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reply_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["batch_id"], ["reply_batches.id"]),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"]),
        sa.ForeignKeyConstraint(["reply_id"], ["replies.id"]),
        sa.PrimaryKeyConstraint("batch_id", "comment_id"),
        sa.UniqueConstraint("batch_id", "item_no", name="uq_reply_batch_items_number"),
    )


def downgrade() -> None:
    op.drop_table("reply_batch_items")
    op.drop_index("ix_comment_batch_queue_ready", table_name="comment_batch_queue")
    op.drop_table("comment_batch_queue")
    op.drop_table("reply_batches")
