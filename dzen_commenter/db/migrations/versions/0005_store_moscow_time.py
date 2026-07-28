"""store timestamps as Moscow wall-clock time

Revision ID: 0005_store_moscow_time
Revises: 0004_add_comment_post_title
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0005_store_moscow_time"
down_revision: Union[str, None] = "0004_add_comment_post_title"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE comments SET posted_at = posted_at + INTERVAL '3 hours' WHERE posted_at IS NOT NULL")
    op.execute("UPDATE comments SET fetched_at = fetched_at + INTERVAL '3 hours' WHERE fetched_at IS NOT NULL")
    op.execute("UPDATE replies SET published_at = published_at + INTERVAL '3 hours' WHERE published_at IS NOT NULL")
    op.execute("UPDATE replies SET created_at = created_at + INTERVAL '3 hours' WHERE created_at IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE comments SET posted_at = posted_at - INTERVAL '3 hours' WHERE posted_at IS NOT NULL")
    op.execute("UPDATE comments SET fetched_at = fetched_at - INTERVAL '3 hours' WHERE fetched_at IS NOT NULL")
    op.execute("UPDATE replies SET published_at = published_at - INTERVAL '3 hours' WHERE published_at IS NOT NULL")
    op.execute("UPDATE replies SET created_at = created_at - INTERVAL '3 hours' WHERE created_at IS NOT NULL")
