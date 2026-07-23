"""add comments.post_title

Revision ID: 0004_add_comment_post_title
Revises: 0003_add_comment_thread_text
Create Date: 2026-07-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_add_comment_post_title"
down_revision: Union[str, None] = "0003_add_comment_thread_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("post_title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "post_title")
