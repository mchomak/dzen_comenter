from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Text
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
    post_title: Mapped[str | None] = mapped_column(Text)
    post_url: Mapped[str | None] = mapped_column(Text)
    thread_text: Mapped[str | None] = mapped_column(Text)


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
    article_context_status: Mapped[str | None] = mapped_column(Text)
    is_cta_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ReplyBatchTable(Base):
    __tablename__ = "reply_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    article_context_status: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    error_reason: Mapped[str | None] = mapped_column(Text)


class CommentBatchQueueTable(Base):
    __tablename__ = "comment_batch_queue"

    comment_id: Mapped[int] = mapped_column(
        ForeignKey("comments.id"), primary_key=True
    )
    post_url: Mapped[str] = mapped_column(Text, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column()
    claimed_batch_id: Mapped[int | None] = mapped_column(ForeignKey("reply_batches.id"))


class ReplyBatchItemTable(Base):
    __tablename__ = "reply_batch_items"

    batch_id: Mapped[int] = mapped_column(
        ForeignKey("reply_batches.id"), primary_key=True
    )
    comment_id: Mapped[int] = mapped_column(
        ForeignKey("comments.id"), primary_key=True
    )
    item_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reply_id: Mapped[int | None] = mapped_column(ForeignKey("replies.id"))
