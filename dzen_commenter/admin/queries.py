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
    post_title: str | None
    post_url: str | None
    post_is_video: bool
    fetched_at: datetime | None
    reply_text: str | None
    reply_status: str | None  # None → ответа ещё нет
    error_reason: str | None
    article_context_status: str | None


def parse_thread_messages(thread_text: str | None) -> list[tuple[str, str]]:
    """Разбор плоской истории ветки в список сообщений `(author, text)`.

    `None`/пустая строка → `[]`. Иначе строка бьётся по `\\n`, пустые строки
    пропускаются, каждая непустая делится по первому `": "` на автора и текст.
    Строка без `": "` возвращается как `("", строка_целиком)` (fallback).
    """
    if not thread_text:
        return []
    messages: list[tuple[str, str]] = []
    for line in thread_text.split("\n"):
        if not line.strip():
            continue
        author, sep, text = line.partition(": ")
        if sep:
            messages.append((author, text))
        else:
            messages.append(("", line))
    return messages


def unique_authors(feed: list[FeedRow]) -> list[str]:
    """Непустые имена авторов из отображаемой ленты, без повторов."""
    return list(dict.fromkeys(row.author for row in feed if row.author))


_POST_PATH_PREFIXES = ("/a/", "/video/watch/")


def _post_url(value: str | None) -> str | None:
    if value and value.startswith(_POST_PATH_PREFIXES):
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
        and parsed.path.startswith(_POST_PATH_PREFIXES)
    ):
        return value
    return None


def _is_video_post_url(value: str | None) -> bool:
    """Ведёт ли ссылка поста на видео/клип, а не на текстовую статью."""
    if not value:
        return False
    try:
        return urlsplit(value).path.startswith("/video/watch/")
    except ValueError:
        return False


STATUS_CATEGORIES = ("published", "generated", "error", "skipped", "no_reply")


def _row_category(row: FeedRow) -> str:
    """Категория статуса строки: `no_reply` при отсутствии ответа."""
    return row.reply_status if row.reply_status is not None else "no_reply"


def fetch_feed(
    engine: Engine,
    limit: int | None = 100,
    status: str | None = None,
    author_query: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    order: str = "desc",
) -> list[FeedRow]:
    """Лента: свежие комментарии сверху (по fetched_at desc), до `limit` записей.

    Для каждого комментария берётся последний связанный reply (с наибольшим id).

    Опциональные фильтры применяются в Python-слое поверх собранной ленты:
    - `status` — одна из категорий `STATUS_CATEGORIES` (`no_reply` → нет ответа);
    - `author_query` — регистронезависимая подстрока по `author`; пустая строка
      или `None` не фильтрует.
    """
    feed = _load_feed(engine, limit, date_from, date_to, order)

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
    for row in _load_feed(engine, limit, None, None, "desc"):
        counts[_row_category(row)] += 1
    return counts


def _load_feed(
    engine: Engine,
    limit: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    order: str,
) -> list[FeedRow]:
    with engine.connect() as conn:
        stmt = select(
            CommentTable.id,
            CommentTable.author,
            CommentTable.text,
            CommentTable.thread_text,
            CommentTable.post_title,
            CommentTable.post_url,
            CommentTable.fetched_at,
        )
        if date_from is not None:
            stmt = stmt.where(CommentTable.fetched_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(CommentTable.fetched_at < date_to)
        if order == "asc":
            stmt = stmt.order_by(CommentTable.fetched_at.asc(), CommentTable.id.asc())
        else:
            stmt = stmt.order_by(CommentTable.fetched_at.desc(), CommentTable.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        comment_rows = conn.execute(stmt).all()

        comment_ids = [row.id for row in comment_rows]
        last_reply: dict[int, object] = {}
        if comment_ids:
            reply_rows = conn.execute(
                select(
                    ReplyTable.comment_id,
                    ReplyTable.generated_text,
                    ReplyTable.status,
                    ReplyTable.error_reason,
                    ReplyTable.article_context_status,
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
        post_url = _post_url(row.post_url)
        feed.append(
            FeedRow(
                author=row.author,
                comment_text=row.text,
                thread_text=row.thread_text,
                post_title=row.post_title,
                post_url=post_url,
                post_is_video=_is_video_post_url(post_url),
                fetched_at=row.fetched_at,
                reply_text=reply.generated_text if reply else None,
                reply_status=reply.status if reply else None,
                error_reason=reply.error_reason if reply else None,
                article_context_status=reply.article_context_status if reply else None,
            )
        )
    return feed
