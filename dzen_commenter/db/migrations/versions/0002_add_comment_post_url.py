"""add comments.post_url

Revision ID: 0002_add_comment_post_url
Revises: 0001_initial
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_add_comment_post_url"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("post_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "post_url")
