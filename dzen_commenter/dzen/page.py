import hashlib
import re
from datetime import datetime, timedelta, timezone

from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import selectors

# Подтверждён данными только формат минут ("N мин", "N м", с русскими склонениями).
# Прочие единицы (часы/дни/недели/месяцы) в снимках не встречены — не гадаем.
# TODO 5B: расширить, когда появится снимок с часами/днями.
_MINUTES_RE = re.compile(
    r"(\d+)\s*(мин\.?|минуту|минуты|минут|м)\b",
    re.IGNORECASE,
)


def synthetic_id(post_href: str, author_href: str, text: str) -> str:
    """Синтетический ключ комментария: в DOM Студии нет реального id."""
    raw = "|".join([post_href, author_href, text.strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_relative_time(text: str | None, now: datetime) -> datetime | None:
    """Best-effort разбор относительного времени Дзена.

    Распознаёт только минуты (единственный подтверждённый данными формат);
    всё прочее (часы/дни/…, «вчера», «только что», пусто, None) → None.
    """
    if not text:
        return None
    match = _MINUTES_RE.search(text)
    if not match:
        return None
    return now - timedelta(minutes=int(match.group(1)))


class DzenStudioPage:
    """Page-object Дзен Студии: чтение комментариев и публикация ответа.

    Принимает уже поднятый Playwright `Page` (инъекция). Никакого создания
    браузера здесь нет. Все селекторы — из `dzen.selectors`.
    """

    def __init__(self, page) -> None:
        self._page = page

    def fetch_comments(self) -> list[Comment]:
        comments: list[Comment] = []
        now = datetime.now(timezone.utc)
        for node, post_href in self._iter_comment_nodes():
            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            author_href = author_link.get_attribute("href") or "" if author_link else ""
            author_text = node.query_selector(selectors.COMMENT_AUTHOR_TEXT)
            text_el = node.query_selector(selectors.COMMENT_TEXT)
            date_el = node.query_selector(selectors.COMMENT_DATE_TEXT)
            text = text_el.inner_text() if text_el else ""
            comments.append(
                Comment(
                    id=None,
                    dzen_comment_id=synthetic_id(post_href, author_href, text),
                    publication_id=0,
                    author=author_text.inner_text() if author_text else "",
                    text=text,
                    parent_comment_id=None,
                    posted_at=parse_relative_time(
                        date_el.inner_text() if date_el else None, now
                    ),
                    fetched_at=now,
                    status=CommentStatus.NEW,
                )
            )
        return comments

    def publish_reply(self, comment: Comment, text: str) -> None:
        for node, post_href in self._iter_comment_nodes():
            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            author_href = author_link.get_attribute("href") or "" if author_link else ""
            text_el = node.query_selector(selectors.COMMENT_TEXT)
            node_text = text_el.inner_text() if text_el else ""
            if synthetic_id(post_href, author_href, node_text) != comment.dzen_comment_id:
                continue
            node.query_selector(selectors.COMMENT_REPLY_BUTTON).click()
            node.query_selector(selectors.REPLY_INPUT).fill(text)
            node.query_selector(selectors.REPLY_SUBMIT).click()
            return
        raise LookupError(
            f"comment {comment.dzen_comment_id!r} not found on page for reply"
        )

    def _iter_comment_nodes(self):
        """Двухуровневый обход: (узел комментария, post_href его группы)."""
        for group in self._page.query_selector_all(selectors.POST_GROUP):
            post_link = group.query_selector(selectors.POST_LINK)
            post_href = post_link.get_attribute("href") or "" if post_link else ""
            for node in group.query_selector_all(selectors.COMMENT_NODE):
                yield node, post_href
