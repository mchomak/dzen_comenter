"""add publication queue lease tokens

Revision ID: 0011_publication_claim_token
Revises: 0010_reply_generation_queue
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_publication_claim_token"
down_revision: Union[str, None] = "0010_reply_generation_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reply_publication_queue",
        sa.Column("claim_token", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reply_publication_queue", "claim_token")
