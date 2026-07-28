"""add replies.article_context_status

Revision ID: 0006_reply_article_context
Revises: 0005_store_moscow_time
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_reply_article_context"
down_revision: Union[str, None] = "0005_store_moscow_time"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("replies", sa.Column("article_context_status", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("replies", "article_context_status")
