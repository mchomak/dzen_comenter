from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine

from dzen_commenter.db.models import CommentTable, ReplyTable


@dataclass(frozen=True)
class FeedRow:
    """Одна строка ленты «Комментарии»: комментарий человека + последний ответ бота."""

    author: str | None
    comment_text: str | None
    post_url: str | None
    fetched_at: datetime | None
    reply_text: str | None
    reply_status: str | None  # None → ответа ещё нет
    error_reason: str | None


def fetch_feed(engine: Engine, limit: int = 100) -> list[FeedRow]:
    """Лента: свежие комментарии сверху (по fetched_at desc), до `limit` записей.

    Для каждого комментария берётся последний связанный reply (с наибольшим id).
    """
    with engine.connect() as conn:
        comment_rows = conn.execute(
            select(
                CommentTable.id,
                CommentTable.author,
                CommentTable.text,
                CommentTable.post_url,
                CommentTable.fetched_at,
            )
            .order_by(CommentTable.fetched_at.desc(), CommentTable.id.desc())
            .limit(limit)
        ).all()

        comment_ids = [row.id for row in comment_rows]
        last_reply: dict[int, object] = {}
        if comment_ids:
            reply_rows = conn.execute(
                select(
                    ReplyTable.comment_id,
                    ReplyTable.generated_text,
                    ReplyTable.status,
                    ReplyTable.error_reason,
                )
                .where(ReplyTable.comment_id.in_(comment_ids))
                .order_by(ReplyTable.id.asc())
            ).all()
            # Замыкаем по возрастанию id: последний (наибольший id) перезаписывает.
            for reply in reply_rows:
                last_reply[reply.comment_id] = reply

    feed: list[FeedRow] = []
    for row in comment_rows:
        reply = last_reply.get(row.id)
        feed.append(
            FeedRow(
                author=row.author,
                comment_text=row.text,
                post_url=row.post_url,
                fetched_at=row.fetched_at,
                reply_text=reply.generated_text if reply else None,
                reply_status=reply.status if reply else None,
                error_reason=reply.error_reason if reply else None,
            )
        )
    return feed
