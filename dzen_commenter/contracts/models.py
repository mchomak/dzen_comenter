from dataclasses import dataclass
from datetime import datetime

from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus


@dataclass
class Publication:
    id: int | None
    dzen_publication_id: str
    title: str
    url: str


@dataclass
class Comment:
    id: int | None
    dzen_comment_id: str
    publication_id: int
    author: str
    text: str
    parent_comment_id: str | None
    posted_at: datetime | None
    fetched_at: datetime | None
    status: CommentStatus
    publication_title: str = ""
    thread_text: str = ""
    post_url: str | None = None


@dataclass
class Reply:
    id: int | None
    comment_id: int
    generated_text: str
    ai_provider: str
    ai_model: str
    status: ReplyStatus
    published_at: datetime | None
    error_reason: str | None
    created_at: datetime | None
