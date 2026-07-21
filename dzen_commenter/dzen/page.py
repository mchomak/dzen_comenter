import hashlib
import re
from datetime import datetime, timedelta, timezone

from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import selectors

_MINUTES_RE = re.compile(
    r"(\d+)\s*(мин\.?|минуту|минуты|минут|м)\b",
    re.IGNORECASE,
)


def synthetic_id(post_href: str, author_href: str, text: str) -> str:
    raw = "|".join([post_href, author_href, text.strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_relative_time(text: str | None, now: datetime) -> datetime | None:
    if not text:
        return None
    match = _MINUTES_RE.search(text)
    if not match:
        return None
    return now - timedelta(minutes=int(match.group(1)))


class DzenStudioPage:
    """Read Dzen Studio comments and publish a reply to a matching node."""

    def __init__(self, page) -> None:
        self._page = page

    def fetch_comments(self) -> list[Comment]:
        comments: list[Comment] = []
        now = datetime.now(timezone.utc)
        for group in self._page.query_selector_all(selectors.POST_GROUP):
            post_link = group.query_selector(selectors.POST_LINK)
            post_href = post_link.get_attribute("href") or "" if post_link else ""
            title_el = group.query_selector(selectors.POST_TITLE)
            publication_title = title_el.inner_text().strip() if title_el else ""
            previous_messages: list[str] = []

            for node in group.query_selector_all(selectors.COMMENT_NODE):
                author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
                author_href = author_link.get_attribute("href") or "" if author_link else ""
                author_el = node.query_selector(selectors.COMMENT_AUTHOR_TEXT)
                text_el = node.query_selector(selectors.COMMENT_TEXT)
                date_el = node.query_selector(selectors.COMMENT_DATE_TEXT)
                author = author_el.inner_text().strip() if author_el else ""
                text = text_el.inner_text().strip() if text_el else ""
                comments.append(
                    Comment(
                        id=None,
                        dzen_comment_id=synthetic_id(post_href, author_href, text),
                        publication_id=0,
                        author=author,
                        text=text,
                        parent_comment_id=self._parent_comment_id(node, post_href),
                        posted_at=parse_relative_time(
                            date_el.inner_text() if date_el else None, now
                        ),
                        fetched_at=now,
                        status=CommentStatus.NEW,
                        publication_title=publication_title,
                        thread_text="\n".join(previous_messages),
                        post_url=post_href,
                    )
                )
                if text:
                    previous_messages.append(f"{author or 'Автор'}: {text}")
        return comments

    @staticmethod
    def _parent_comment_id(node, post_href: str) -> str | None:
        try:
            parent = node.evaluate(
                """
                (node) => {
                    const container = node.closest(
                        '[class*="editor--root-comment__commentNode-"]'
                    );
                    const block = container?.querySelector(
                        '[class*="editor--comment__block-"]'
                    );
                    if (!block) return null;
                    const author = block.querySelector(
                        '[class*="editor--comment__nameLink-"]'
                    );
                    const text = block.querySelector(
                        'p[aria-label="Текст комментария"]'
                    );
                    return {
                        authorHref: author?.getAttribute('href') || '',
                        text: text?.innerText || '',
                    };
                }
                """
            )
        except Exception:
            return None
        if not parent or not parent.get("text"):
            return None
        return synthetic_id(post_href, parent.get("authorHref", ""), parent["text"])

    def publish_reply(
        self, comment: Comment, text: str, *, auto_publish: bool
    ) -> None:
        for node, post_href in self._iter_comment_nodes():
            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            author_href = author_link.get_attribute("href") or "" if author_link else ""
            text_el = node.query_selector(selectors.COMMENT_TEXT)
            node_text = text_el.inner_text() if text_el else ""
            if synthetic_id(post_href, author_href, node_text) != comment.dzen_comment_id:
                continue
            node.query_selector(selectors.COMMENT_REPLY_BUTTON).click()
            node.query_selector(selectors.REPLY_INPUT).fill(text)
            if auto_publish:
                node.query_selector(selectors.REPLY_SUBMIT).click()
            else:
                self._page.wait_for_timeout(5_000)
            return
        raise LookupError(f"comment {comment.dzen_comment_id!r} not found on page for reply")

    def _iter_comment_nodes(self):
        for group in self._page.query_selector_all(selectors.POST_GROUP):
            post_link = group.query_selector(selectors.POST_LINK)
            post_href = post_link.get_attribute("href") or "" if post_link else ""
            for node in group.query_selector_all(selectors.COMMENT_NODE):
                yield node, post_href
