from datetime import datetime, timezone

from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import selectors


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
        for element in self._page.query_selector_all(selectors.COMMENT_ITEM):
            author_el = element.query_selector(selectors.COMMENT_AUTHOR)
            text_el = element.query_selector(selectors.COMMENT_TEXT)
            comments.append(
                Comment(
                    id=None,
                    dzen_comment_id=element.get_attribute(selectors.COMMENT_ID_ATTR),
                    publication_id=0,
                    author=author_el.inner_text() if author_el else "",
                    text=text_el.inner_text() if text_el else "",
                    parent_comment_id=element.get_attribute(
                        selectors.COMMENT_PARENT_ATTR
                    ),
                    posted_at=self._parse_posted_at(
                        element.get_attribute(selectors.COMMENT_POSTED_ATTR)
                    ),
                    fetched_at=now,
                    status=CommentStatus.NEW,
                )
            )
        return comments

    def publish_reply(self, comment: Comment, text: str) -> None:
        self._page.fill(selectors.REPLY_INPUT, text)
        self._page.click(selectors.REPLY_SUBMIT)

    @staticmethod
    def _parse_posted_at(raw: str | None) -> datetime | None:
        if not raw:
            return None
        return datetime.fromisoformat(raw)
