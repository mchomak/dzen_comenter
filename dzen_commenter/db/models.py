from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PublicationTable(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(primary_key=True)
    dzen_publication_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)


class CommentTable(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    dzen_comment_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    publication_id: Mapped[int] = mapped_column(
        ForeignKey("publications.id"), nullable=False
    )
    author: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    parent_comment_id: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column()
    fetched_at: Mapped[datetime | None] = mapped_column()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    post_url: Mapped[str | None] = mapped_column(Text)


class ReplyTable(Base):
    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(
        ForeignKey("comments.id"), nullable=False
    )
    generated_text: Mapped[str | None] = mapped_column(Text)
    ai_provider: Mapped[str | None] = mapped_column(Text)
    ai_model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column()
    error_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column()
