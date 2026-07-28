import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import selectors
from dzen_commenter.time_utils import moscow_now

_MINUTES_RE = re.compile(
    r"(\d+)\s*(мин\.?|минуту|минуты|минут|м)\b",
    re.IGNORECASE,
)


def synthetic_id(post_href: str, author_href: str, text: str) -> str:
    raw = "|".join([post_href, author_href, text.strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_POST_PATH_PREFIXES = ("/a/", "/video/watch/")


def _post_url(post_href: str) -> str | None:
    if post_href.startswith(_POST_PATH_PREFIXES):
        return f"https://dzen.ru{post_href}"
    try:
        parsed = urlsplit(post_href)
    except ValueError:
        return None
    if (
        parsed.scheme == "https"
        and parsed.hostname in {"dzen.ru", "www.dzen.ru"}
        and parsed.path.startswith(_POST_PATH_PREFIXES)
    ):
        suffix = f"?{parsed.query}" if parsed.query else ""
        return f"https://dzen.ru{parsed.path}{suffix}"
    return None


def is_video_post_url(url: str | None) -> bool:
    """Ведёт ли ссылка поста на видео/клип (/video/watch/…), а не на текстовую статью."""
    if not url:
        return False
    try:
        return urlsplit(url).path.startswith("/video/watch/")
    except ValueError:
        return False


def _post_href(group) -> str:
    post_link = group.query_selector(selectors.POST_LINK)
    post_href = post_link.get_attribute("href") or "" if post_link else ""
    if _post_url(post_href) is not None:
        return post_href
    fallback = group.query_selector(selectors.POST_LINK_FALLBACK)
    fallback_href = fallback.get_attribute("href") or "" if fallback else ""
    return fallback_href if _post_url(fallback_href) is not None else ""


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
        self._article_text_by_url: dict[str, str | None] = {}

    def fetch_article_text(self, post_url: str) -> str | None:
        if not post_url or is_video_post_url(post_url):
            return None
        if post_url in self._article_text_by_url:
            return self._article_text_by_url[post_url]

        article_page = self._page.context.new_page()
        try:
            article_page.goto(post_url, wait_until="domcontentloaded")
            text = ""
            for selector in ("article", "main", '[class*="article"]'):
                article = article_page.query_selector(selector)
                candidate = article.inner_text().strip() if article else ""
                if candidate:
                    text = candidate
                    break
        except Exception:
            text = ""
        finally:
            article_page.close()
        self._article_text_by_url[post_url] = text or None
        return self._article_text_by_url[post_url]

    def fetch_comments(self) -> list[Comment]:
        comments: list[Comment] = []
        now = moscow_now()
        for group in self._page.query_selector_all(selectors.POST_GROUP):
            post_href = _post_href(group)
            if not post_href:
                # dzen_comment_id hashes in post_href, so a comment scraped once
                # with a real link and once with a failed extraction would get two
                # different ids — a phantom duplicate with no post_url, potentially
                # a duplicate reply. Skip the group; a later cycle retries it.
                continue
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
                        post_url=_post_url(post_href),
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
            post_href = _post_href(group)
            for node in group.query_selector_all(selectors.COMMENT_NODE):
                yield node, post_href
