"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dzen_publication_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "dzen_publication_id", name="uq_publications_dzen_publication_id"
        ),
    )
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dzen_comment_id", sa.Text(), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("parent_comment_id", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "dzen_comment_id", name="uq_comments_dzen_comment_id"
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            name="fk_comments_publication_id_publications",
        ),
    )
    op.create_table(
        "replies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("generated_text", sa.Text(), nullable=True),
        sa.Column("ai_provider", sa.Text(), nullable=True),
        sa.Column("ai_model", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["comments.id"],
            name="fk_replies_comment_id_comments",
        ),
    )


def downgrade() -> None:
    op.drop_table("replies")
    op.drop_table("comments")
    op.drop_table("publications")
