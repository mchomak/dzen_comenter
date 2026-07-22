"""add comments.thread_text

Revision ID: 0003_add_comment_thread_text
Revises: 0002_add_comment_post_url
Create Date: 2026-07-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_comment_thread_text"
down_revision: Union[str, None] = "0002_add_comment_post_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("thread_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "thread_text")
