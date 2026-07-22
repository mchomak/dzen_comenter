from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.engine import Engine

from dzen_commenter.db.models import CommentTable, ReplyTable


@dataclass(frozen=True)
class FeedRow:
    """Одна строка ленты «Комментарии»: комментарий человека + последний ответ бота."""

    author: str | None
    comment_text: str | None
    thread_text: str | None
    post_url: str | None
    fetched_at: datetime | None
    reply_text: str | None
    reply_status: str | None  # None → ответа ещё нет
    error_reason: str | None


def _post_url(value: str | None) -> str | None:
    if value and value.startswith("/a/"):
        return f"https://dzen.ru{value}"
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme == "https"
        and parsed.hostname in {"dzen.ru", "www.dzen.ru"}
        and parsed.path.startswith("/a/")
    ):
        return value
    return None


STATUS_CATEGORIES = ("published", "generated", "error", "skipped", "no_reply")


def _row_category(row: FeedRow) -> str:
    """Категория статуса строки: `no_reply` при отсутствии ответа."""
    return row.reply_status if row.reply_status is not None else "no_reply"


def fetch_feed(
    engine: Engine,
    limit: int = 100,
    status: str | None = None,
    author_query: str | None = None,
) -> list[FeedRow]:
    """Лента: свежие комментарии сверху (по fetched_at desc), до `limit` записей.

    Для каждого комментария берётся последний связанный reply (с наибольшим id).

    Опциональные фильтры применяются в Python-слое поверх собранной ленты:
    - `status` — одна из категорий `STATUS_CATEGORIES` (`no_reply` → нет ответа);
    - `author_query` — регистронезависимая подстрока по `author`; пустая строка
      или `None` не фильтрует.
    """
    feed = _load_feed(engine, limit)

    if status:
        feed = [row for row in feed if _row_category(row) == status]

    if author_query:
        needle = author_query.casefold()
        feed = [
            row
            for row in feed
            if row.author is not None and needle in row.author.casefold()
        ]

    return feed


def fetch_status_counts(engine: Engine, limit: int = 100) -> dict[str, int]:
    """Подсчёт строк ленты (последние `limit`) по 5 категориям статуса.

    Сумма значений равна числу строк ленты.
    """
    counts = {category: 0 for category in STATUS_CATEGORIES}
    for row in _load_feed(engine, limit):
        counts[_row_category(row)] += 1
    return counts


def _load_feed(engine: Engine, limit: int) -> list[FeedRow]:
    with engine.connect() as conn:
        comment_rows = conn.execute(
            select(
                CommentTable.id,
                CommentTable.author,
                CommentTable.text,
                CommentTable.thread_text,
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
                thread_text=row.thread_text,
                post_url=_post_url(row.post_url),
                fetched_at=row.fetched_at,
                reply_text=reply.generated_text if reply else None,
                reply_status=reply.status if reply else None,
                error_reason=reply.error_reason if reply else None,
            )
        )
    return feed
