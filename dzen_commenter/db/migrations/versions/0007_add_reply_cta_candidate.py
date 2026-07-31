"""add replies.is_cta_candidate

Revision ID: 0007_cta_candidate
Revises: 0006_reply_article_context
Create Date: 2026-07-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_cta_candidate"
down_revision: Union[str, None] = "0006_reply_article_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "replies",
        sa.Column(
            "is_cta_candidate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("replies", "is_cta_candidate")
