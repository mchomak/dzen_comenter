import hashlib
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from dzen_commenter.config.runtime_config import (
    DEFAULT_BOT_ACCOUNT_NAME,
    is_bot_account_author,
)
from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.errors import SourceCommentUnavailableError
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
_ARTICLE_CONTENT_SELECTOR = '[class*="article-render__container"]'
_PROMOTIONAL_ARTICLE_MARKERS = (
    "domeo",
    "domeo.ru",
    "калькулятор",
    "подписывайтесь",
    "подпишитесь",
    "читайте ещё",
    "читайте еще",
    "рекомендую эти статьи",
    "читайте больше на канале",
    "бесплатно рассчита",
    "посмотрите проект",
    "посмотреть больше работ",
    "советует начать свой ремонт",
)
_REPLY_SEARCH_MAX_SCROLLS = 20
_REPLY_SEARCH_WAIT_MS = 500
_REPLY_SEARCH_SCROLL_DELTA_Y = 1_000
_REPLY_SUBMIT_ACK_TIMEOUT_MS = 10_000
_REPLY_SUBMIT_BUTTON_TIMEOUT_MS = 10_000
_REPLY_EXPANSION_TIMEOUT_MS = 10_000
_SUBMIT_TRACE_LIMIT = 5


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

    def __init__(
        self,
        page: Any | Callable[[], Any],
        *,
        bot_account_name_provider: Callable[[], str] | None = None,
    ) -> None:
        self._page_source = page
        self._bot_account_name_provider = (
            bot_account_name_provider or (lambda: DEFAULT_BOT_ACCOUNT_NAME)
        )
        self._article_text_by_url: dict[str, str | None] = {}

    @property
    def _page(self) -> Any:
        if callable(self._page_source):
            return self._page_source()
        return self._page_source

    def fetch_article_text(self, post_url: str) -> str | None:
        if not post_url or is_video_post_url(post_url):
            return None
        if post_url in self._article_text_by_url:
            return self._article_text_by_url[post_url]

        article_page = self._page.context.new_page()
        try:
            article_page.goto(post_url, wait_until="domcontentloaded")
            text = self._extract_article_text(article_page)
        except Exception:
            text = ""
        finally:
            article_page.close()
        self._article_text_by_url[post_url] = text or None
        return self._article_text_by_url[post_url]

    @staticmethod
    def _extract_article_text(article_page: Any) -> str:
        wait_for_selector = getattr(article_page, "wait_for_selector", None)
        if wait_for_selector is not None:
            try:
                wait_for_selector(_ARTICLE_CONTENT_SELECTOR, timeout=5_000)
            except Exception:
                pass

        try:
            article_content = article_page.evaluate(
                """
            () => {
                const root = document.querySelector('[class*="article-render__container"]');
                if (!root) return null;
                const text = (node) => (node.innerText || '').trim();
                const title = text(document.querySelector('h1'));
                const blocks = [];
                for (const node of root.children) {
                    if (!node.matches('p, h2, h3, h4, li, blockquote, figcaption')) continue;
                    const value = text(node);
                    if (value) blocks.push({ tag: node.tagName, text: value });
                }
                return { title, blocks };
            }
            """
            )
        except Exception:
            article_content = None
        if not article_content:
            article = article_page.query_selector("article")
            return article.inner_text().strip() if article else ""

        parts = []
        title = article_content.get("title", "")
        if title:
            parts.append(title)
        for block in article_content.get("blocks", []):
            text = block.get("text", "").strip()
            if text and not DzenStudioPage._is_article_noise_block(
                text, block.get("tag", "")
            ):
                parts.append(text)
        return "\n\n".join(parts)

    @staticmethod
    def _is_article_noise_block(text: str, tag: str) -> bool:
        if tag.upper() == "FIGCAPTION":
            return True
        normalized = text.casefold()
        return any(marker in normalized for marker in _PROMOTIONAL_ARTICLE_MARKERS)

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
                (node, threadSelector) => {
                    const container = node.closest(
                        threadSelector
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
                """,
                selectors.COMMENT_THREAD,
            )
        except Exception:
            return None
        if not parent or not parent.get("text"):
            return None
        return synthetic_id(post_href, parent.get("authorHref", ""), parent["text"])

    def publish_reply(
        self, comment: Comment, text: str, *, auto_publish: bool
    ) -> None:
        node = self._find_comment_node_with_scroll(comment.dzen_comment_id)
        if node is None:
            raise SourceCommentUnavailableError(
                f"comment {comment.dzen_comment_id!r} not found on page for reply"
            )

        if auto_publish and self._has_published_reply(node, text):
            return

        if not auto_publish:
            self._submit_reply(node, text, auto_publish=False)
            return

        page = self._page
        mutations: list[dict[str, Any]] = []
        truncated = False
        creation_request: Any | None = None
        creation_payload: dict[str, Any] | None = None
        creation_response: Any | None = None
        normalized_text = " ".join(text.split())

        def on_request(request: Any) -> None:
            nonlocal truncated, creation_request, creation_payload
            try:
                parsed = urlsplit(request.url)
                method = request.method.upper()
                host = parsed.hostname or ""
                if method == "GET" or not (
                    host == "dzen.ru" or host.endswith(".dzen.ru")
                ):
                    return
                if method == "POST" and creation_request is None:
                    try:
                        payload = request.post_data_json
                    except Exception:
                        payload = None
                    if (
                        isinstance(payload, dict)
                        and isinstance(payload.get("text"), str)
                        and " ".join(payload["text"].split()) == normalized_text
                    ):
                        creation_request = request
                        creation_payload = payload
                if len(mutations) >= _SUBMIT_TRACE_LIMIT:
                    truncated = True
                    return
                path_sha256 = hashlib.sha256(
                    parsed.path.encode("utf-8")
                ).hexdigest()[:12]
                mutations.append(
                    {
                        "request": request,
                        "method": re.sub(r"[^A-Z]", "_", method[:12]),
                        "host": host,
                        "path_sha256": path_sha256,
                        "status": "pending",
                    }
                )
            except Exception:
                return

        def on_response(response: Any) -> None:
            nonlocal creation_response
            try:
                if response.request is creation_request:
                    creation_response = response
                for mutation in mutations:
                    if mutation["request"] is response.request:
                        mutation["status"] = str(int(response.status))
                        break
            except Exception:
                return

        acknowledged = False

        def submission_started() -> bool:
            nonlocal acknowledged
            if (
                self._creation_response_outcome(
                    creation_response, creation_payload, normalized_text
                )
                == "accepted"
            ):
                return True
            acknowledged = acknowledged or self._has_published_reply(node, text)
            return acknowledged

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            self._submit_reply(
                node,
                text,
                auto_publish=True,
                submission_started=submission_started,
            )
            acknowledged = acknowledged or self._has_published_reply(node, text)
            for _ in range(_REPLY_SUBMIT_ACK_TIMEOUT_MS // _REPLY_SEARCH_WAIT_MS):
                if creation_response is not None:
                    break
                page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
                if not acknowledged:
                    acknowledged = self._has_published_reply(node, text)

            creation_outcome = self._creation_response_outcome(
                creation_response, creation_payload, normalized_text
            )

            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
            node = self._find_comment_node_with_scroll(comment.dzen_comment_id)
            if node is None or not self._has_published_reply(node, text):
                count = None if node is None else node.evaluate(
                    """(node, threadSelector) =>
                        node.closest(threadSelector)?.querySelectorAll(
                            '[class*="editor--comment__block-"]'
                        ).length ?? null""",
                    selectors.COMMENT_THREAD,
                )
                outcomes = ", ".join(
                    f"{item['method']} {item['host']} "
                    f"path_sha256={item['path_sha256']} {item['status']}"
                    for item in mutations
                ) or "none"
                reply_count = max(0, count - 1) if count is not None else "unknown"
                pending_responses = sum(
                    item["status"] == "pending" for item in mutations
                )
                raise RuntimeError(
                    "reply not confirmed during post-reload verification; "
                    f"creation_outcome={creation_outcome}; "
                    f"ack_before_reload={str(acknowledged).lower()}; "
                    f"source_found={str(node is not None).lower()}; "
                    f"target_reply_count={reply_count}; "
                    f"mutations=[{outcomes}]; "
                    f"pending_responses={pending_responses}; "
                    f"truncated={str(truncated).lower()}"
                )
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

    @staticmethod
    def _creation_response_outcome(
        response: Any | None, payload: dict[str, Any] | None, normalized_text: str
    ) -> str:
        if payload is None:
            return "missing_request"
        if response is None:
            return "missing_response"
        try:
            if not 200 <= int(response.status) < 300:
                return "non_2xx"
            body = response.json()
            if not isinstance(body, dict) or body.get("status") != "ok":
                return "invalid_response"
            comments = body.get("comments")
            if not isinstance(comments, list) or len(comments) != 1:
                return "invalid_response"
            created = comments[0]
            if not isinstance(created, dict):
                return "invalid_response"
            if not str(created.get("id") or "").strip():
                return "missing_created_id"
            if not isinstance(created.get("text"), str) or (
                " ".join(created["text"].split()) != normalized_text
            ):
                return "text_mismatch"
            if created.get("visibility") != "visible":
                return "visibility_mismatch"
            if not all(
                created.get(field) == payload[field]
                for field in ("publisherId", "documentId", "rootId", "replyToId")
                if field in payload
            ):
                return "relation_mismatch"
            return "accepted"
        except Exception:
            return "invalid_response"

    def _find_comment_node_with_scroll(self, comment_id: str):
        node, _ = self._find_comment_node(comment_id)
        if node is not None:
            return node

        try:
            for _ in range(_REPLY_SEARCH_MAX_SCROLLS):
                self._page.mouse.wheel(0, _REPLY_SEARCH_SCROLL_DELTA_Y)
                self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
                node, _ = self._find_comment_node(comment_id)
                if node is not None:
                    return node
            return None
        finally:
            try:
                self._page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

    def _has_published_reply(self, node: Any, text: str) -> bool:
        bot_account_name = self._bot_account_name_provider()
        if not bot_account_name.strip():
            raise RuntimeError(
                "source comment thread is uninspectable: bot author is not configured"
            )

        read_replies = """
            (node, threadSelector) => {
                const thread = node.closest(threadSelector);
                if (!thread) return null;
                return Array.from(
                    thread.querySelectorAll('[class*="editor--comment__block-"]')
                ).filter((block) => block !== node).map((block) => ({
                    author: block.querySelector(
                        '[class*="editor--comment__nameText-"]'
                    )?.innerText || '',
                    text: block.querySelector(
                        'p[aria-label="Текст комментария"]'
                    )?.innerText || '',
                }));
            }
            """

        normalized_text = " ".join(text.split())
        for check in range(2):
            replies = node.evaluate(read_replies, selectors.COMMENT_THREAD)
            if replies is None:
                raise RuntimeError(
                    "source comment thread is uninspectable: wrapper not found"
                )
            if any(
                is_bot_account_author(reply.get("author"), bot_account_name)
                and " ".join(reply.get("text", "").split()) == normalized_text
                for reply in replies
            ):
                return True
            if check == 0 and self._expand_thread(node):
                continue
            return False
        return False

    def _expand_thread(self, node: Any) -> bool:
        state = node.evaluate(
            """
            (node, selectors) => {
                const thread = node.closest(selectors.thread);
                if (!thread) return null;
                const count = thread.querySelectorAll(
                    '[class*="editor--comment__block-"]'
                ).length;
                const button = thread.querySelector(selectors.more);
                if (!button) return {count, expanded: false};
                button.click();
                return {count, expanded: true};
            }
            """,
            {"thread": selectors.COMMENT_THREAD, "more": selectors.COMMENT_OPEN_MORE},
        )
        if state is None:
            raise RuntimeError("source comment thread is uninspectable: wrapper not found")
        if not state["expanded"]:
            return False

        for _ in range(_REPLY_EXPANSION_TIMEOUT_MS // _REPLY_SEARCH_WAIT_MS):
            count = node.evaluate(
                """
                (node, threadSelector) => node.closest(threadSelector)?.querySelectorAll(
                    '[class*="editor--comment__block-"]'
                ).length ?? null
                """,
                selectors.COMMENT_THREAD,
            )
            if count is None:
                raise RuntimeError("source comment thread is uninspectable: wrapper not found")
            if count > state["count"]:
                self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
                return True
            self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
        raise RuntimeError("source comment thread is uninspectable: replies did not expand")

    def _find_comment_node(self, comment_id: str):
        seen_ids: set[str] = set()
        for node, post_href in self._iter_comment_nodes():
            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            author_href = author_link.get_attribute("href") or "" if author_link else ""
            text_el = node.query_selector(selectors.COMMENT_TEXT)
            node_text = text_el.inner_text() if text_el else ""
            node_id = synthetic_id(post_href, author_href, node_text)
            seen_ids.add(node_id)
            if node_id == comment_id:
                return node, seen_ids
        return None, seen_ids

    def _submit_reply(
        self,
        node,
        text: str,
        *,
        auto_publish: bool,
        submission_started: Callable[[], bool] | None = None,
    ) -> None:
        node.query_selector(selectors.COMMENT_REPLY_BUTTON).click()
        node.query_selector(selectors.REPLY_INPUT).fill(text)
        if auto_publish:
            send_button = node.query_selector(selectors.REPLY_SUBMIT)
            if send_button is None:
                raise RuntimeError("Dzen reply send button was not found after filling")
            send_button.click()
            if self._wait_for_send_button_to_hide(node):
                return

            if submission_started is None or not submission_started():
                send_button = node.query_selector(selectors.REPLY_SUBMIT)
                if send_button is not None and send_button.is_visible():
                    send_button.click()

            if not self._wait_for_send_button_to_hide(node):
                raise RuntimeError(
                    "Dzen reply send button remained visible after submit; page was not reloaded"
                )
        else:
            self._page.wait_for_timeout(5_000)

    def _wait_for_send_button_to_hide(self, node: Any) -> bool:
        for _ in range(
            _REPLY_SUBMIT_BUTTON_TIMEOUT_MS // _REPLY_SEARCH_WAIT_MS
        ):
            send_button = node.query_selector(selectors.REPLY_SUBMIT)
            if send_button is None or not send_button.is_visible():
                return True
            self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
        send_button = node.query_selector(selectors.REPLY_SUBMIT)
        return send_button is None or not send_button.is_visible()

    def _iter_comment_nodes(self):
        for group in self._page.query_selector_all(selectors.POST_GROUP):
            post_href = _post_href(group)
            for node in group.query_selector_all(selectors.COMMENT_NODE):
                yield node, post_href
