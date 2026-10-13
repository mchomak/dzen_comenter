"""add durable AI call reservations

Revision ID: 0012_ai_call_reservations
Revises: 0011_publication_claim_token
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_ai_call_reservations"
down_revision: Union[str, None] = "0011_publication_claim_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_call_reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_call_reservations_reserved_at",
        "ai_call_reservations",
        ["reserved_at"],
    )

    # Reply timestamps are naive Moscow times. Seed only the same countable
    # statuses used by the former hourly counter, and only its rolling window.
    op.execute(
        sa.text(
            "INSERT INTO ai_call_reservations (reserved_at) "
            "SELECT created_at FROM replies "
            "WHERE created_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow') "
            "- INTERVAL '1 hour' AND status IN "
            "('generated', 'published', 'error', 'skipped', 'unconfirmed')"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_call_reservations_reserved_at", table_name="ai_call_reservations"
    )
    op.drop_table("ai_call_reservations")
