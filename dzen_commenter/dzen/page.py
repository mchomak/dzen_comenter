import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from dzen_commenter.config.runtime_config import (
    DEFAULT_BOT_ACCOUNT_NAME,
    is_bot_account_author,
)
from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.errors import (
    PublicationUnconfirmedError,
    SourceCommentUnavailableError,
)
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import selectors
from dzen_commenter.time_utils import moscow_now

logger = logging.getLogger(__name__)

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
_REPLY_SEARCH_MAX_SCROLLS = 40
_REPLY_SEARCH_WAIT_MS = 750
_REPLY_SEARCH_SCROLL_DELTA_Y = 1_000
_STUDIO_FEED_MAX_SCAN_PASSES = 40
_STUDIO_FEED_STABLE_PASSES = 3
_STUDIO_FEED_COUNTS_SCRIPT = """
(selectors) => {
    const groups = Array.from(document.querySelectorAll(selectors.group));
    return {
        group_count: groups.length,
        comment_count: groups.reduce(
            (total, group) => total + group.querySelectorAll(selectors.comment).length,
            0,
        ),
        button_count: document.querySelectorAll(selectors.more).length,
    };
}
"""
_STUDIO_FEED_SCROLL_SCRIPT = """
(selectors) => {
    const groups = Array.from(document.querySelectorAll(selectors.group));
    let lastItem = groups[groups.length - 1] || null;
    for (let index = groups.length - 1; index >= 0; index -= 1) {
        const comments = groups[index].querySelectorAll(selectors.comment);
        if (comments.length) {
            lastItem = comments[comments.length - 1];
            break;
        }
    }
    lastItem?.scrollIntoView({ block: "end", behavior: "instant" });
}
"""
_STUDIO_REPLY_GROUP_INDICES_SCRIPT = """
(selectors) => Array.from(document.querySelectorAll(selectors.group))
    .flatMap((group, index) =>
        group.querySelector(selectors.more) ? [index] : []
    )
"""
_REPLY_BUTTON_KEY_SCRIPT = """
(node) => {
    const commentSelector = '[class*="editor--comment__block-"]';
    const pageThreadSelector = '[class*="editor--comments-page__commentNode-"]';
    const rootThreadSelector = '[class*="editor--root-comment__commentNode-"]';
    const threadSelector = `${pageThreadSelector}, ${rootThreadSelector}`;
    const groupSelector = '[data-testid="comment"], '
        + '[class*="editor--comments-page__groupByPost-"]';
    const openMoreSelector = 'button[class*="editor--root-comment__openMoreButton-"]';
    const directComment = node.closest(commentSelector);
    const thread = node.closest(threadSelector)
        || directComment?.closest(threadSelector);
    const group = thread?.closest(groupSelector)
        || node.closest(groupSelector)
        || directComment?.closest(groupSelector);
    const scope = thread || group;
    if (!scope || !group) return null;

    let comments = Array.from(scope.querySelectorAll(commentSelector));
    const ownerForControl = (control, candidates) => {
        const directOwner = control.closest(commentSelector);
        if (directOwner) return directOwner;
        let precedingOwner = null;
        for (const candidate of candidates) {
            if (candidate.compareDocumentPosition(control)
                & Node.DOCUMENT_POSITION_FOLLOWING) {
                precedingOwner = candidate;
            }
        }
        return precedingOwner;
    };
    let comment = directComment || ownerForControl(node, comments);
    if (!comment) {
        comments = Array.from(group.querySelectorAll(commentSelector));
        comment = ownerForControl(node, comments);
    }

    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const postHref = group.querySelector(
        '[class*="editor--comments-page__postContainer-"] a[href]'
    )?.getAttribute('href') || '';
    const groupIndex = Array.from(document.querySelectorAll(groupSelector))
        .indexOf(group);
    const threadIndex = thread
        ? Array.from(group.querySelectorAll(threadSelector)).indexOf(thread)
        : -1;
    const fallbackKey = () => {
        const groupControls = Array.from(
            group.querySelectorAll(openMoreSelector)
        );
        const controlIndex = groupControls.indexOf(node);
        if (controlIndex < 0) return null;
        const controlClass = String(node.className || '').trim()
            .replace(/\\s+/g, ' ');
        return `fallback:${JSON.stringify([
            postHref, groupIndex, threadIndex, controlClass,
            normalize(node.innerText), controlIndex,
        ])}`;
    };
    if (!comment) return fallbackKey();

    const signature = (candidate) => JSON.stringify([
        candidate.querySelector('[class*="editor--comment__nameLink-"]')
            ?.getAttribute('href') || '',
        normalize(candidate.querySelector('[class*="editor--comment__text-"]')
            ?.innerText),
    ]);
    const ownSignature = signature(comment);
    let occurrence = 0;
    for (const candidate of comments) {
        if (signature(candidate) !== ownSignature) continue;
        if (candidate === comment) break;
        occurrence++;
    }
    const controlsForComment = Array.from(
        group.querySelectorAll(openMoreSelector)
    ).filter((control) => ownerForControl(control, comments) === comment);
    const controlOccurrence = controlsForComment.indexOf(node);
    if (controlOccurrence < 0) return fallbackKey();
    return JSON.stringify([
        postHref, groupIndex, threadIndex, ownSignature, occurrence,
        controlOccurrence,
    ]);
}
"""
_REPLY_CONTROL_SNAPSHOT_SCRIPT = """
(selectors) => {
    const keyFor = REPLY_BUTTON_KEY_FUNCTION;
    const groups = Array.from(document.querySelectorAll(selectors.group));
    const allControls = Array.from(document.querySelectorAll(selectors.more));
    const controlKeys = window.__dzenReplyControlKeys || new WeakMap();
    window.__dzenReplyControlKeys = controlKeys;
    const groupIndexes = selectors.scopeIndex === null
        ? groups.map((_group, index) => index)
        : [selectors.scopeIndex];
    const controls = [];

    for (const groupIndex of groupIndexes) {
        const group = groups[groupIndex];
        if (!group) {
            return {identity_matches: false, post_href: "", controls: []};
        }
        const postLink = group.querySelector(selectors.postLink)
            || group.querySelector(selectors.postLinkFallback);
        const postHref = postLink?.getAttribute("href") || "";
        if (selectors.expectedPostHref && postHref !== selectors.expectedPostHref) {
            return {identity_matches: false, post_href: postHref, controls: []};
        }

        const buttons = Array.from(group.querySelectorAll(selectors.more));
        const visibleCommentCount = Array.from(
            group.querySelectorAll(selectors.comment)
        ).filter((comment) => {
            const rect = comment.getBoundingClientRect();
            const style = getComputedStyle(comment);
            return rect.width > 0
                && rect.height > 0
                && style.display !== "none"
                && style.visibility !== "hidden";
        }).length;
        buttons.forEach((button, buttonIndex) => {
            const key = keyFor(button);
            if (typeof key === "string" && key) {
                controlKeys.set(button, key);
            } else {
                controlKeys.delete(button);
            }
            const rect = button.getBoundingClientRect();
            const style = getComputedStyle(button);
            controls.push({
                group_index: groupIndex,
                button_index: buttonIndex,
                global_index: allControls.indexOf(button),
                post_href: postHref,
                key,
                visible: rect.width > 0
                    && rect.height > 0
                    && style.display !== "none"
                    && style.visibility !== "hidden",
                group_comment_count: visibleCommentCount,
                class_name: String(button.className || "").trim()
                    .replace(/\\s+/g, " "),
                text: String(button.innerText || "").trim(),
            });
        });
    }
    return {identity_matches: true, post_href: "", controls};
}
""".replace("REPLY_BUTTON_KEY_FUNCTION", _REPLY_BUTTON_KEY_SCRIPT)
_REPLY_CONTROL_CLICK_SCRIPT = """
(selectors) => {
    const groups = Array.from(document.querySelectorAll(selectors.group));
    const group = groups[selectors.groupIndex];
    if (!group) return false;
    const postLink = group.querySelector(selectors.postLink)
        || group.querySelector(selectors.postLinkFallback);
    const postHref = postLink?.getAttribute("href") || "";
    if (postHref !== selectors.expectedPostHref) return false;

    const button = group.querySelectorAll(selectors.more)[selectors.buttonIndex];
    if (!button) return false;
    const controlKeys = window.__dzenReplyControlKeys;
    if (selectors.expectedKey
        && !selectors.expectedKey.startsWith("fallback:")
        && controlKeys?.get(button) !== selectors.expectedKey) {
        return false;
    }
    const buttonClass = String(button.className || "").trim()
        .replace(/\\s+/g, " ");
    if (buttonClass !== selectors.expectedClass) {
        return false;
    }
    if (String(button.innerText || "").trim() !== selectors.expectedText) {
        return false;
    }
    const rect = button.getBoundingClientRect();
    const style = getComputedStyle(button);
    if (rect.width <= 0 || rect.height <= 0
        || style.display === "none" || style.visibility === "hidden") {
        return false;
    }
    window.setTimeout(() => {
        if (!group.isConnected || !button.isConnected) return;
        const currentPostLink = group.querySelector(selectors.postLink)
            || group.querySelector(selectors.postLinkFallback);
        const currentPostHref = currentPostLink?.getAttribute("href") || "";
        const currentButton = group.querySelectorAll(selectors.more)[
            selectors.buttonIndex
        ];
        if (currentPostHref !== selectors.expectedPostHref
            || currentButton !== button
            || String(button.className || "").trim().replace(/\\s+/g, " ")
                !== selectors.expectedClass
            || String(button.innerText || "").trim() !== selectors.expectedText) {
            return;
        }
        const currentRect = button.getBoundingClientRect();
        const currentStyle = getComputedStyle(button);
        if (currentRect.width <= 0 || currentRect.height <= 0
            || currentStyle.display === "none"
            || currentStyle.visibility === "hidden") {
            return;
        }
        button.click();
    }, 25);
    return true;
}
"""
_REPLY_SUBMIT_ACK_TIMEOUT_MS = 30_000
_REPLY_SUBMIT_BUTTON_TIMEOUT_MS = 30_000
_REPLY_EXPANSION_TIMEOUT_MS = 30_000
_REPLY_EXPANSION_OPERATION_TIMEOUT_MS = 10 * 60_000
_REPLY_EXPANSION_CLICK_TIMEOUT_MS = 5_000
_REPLY_EXPANSION_RENDER_TIMEOUT_MS = 2_000
_REPLY_EXPANSION_RENDER_POLL_MS = 1_000
_REPLY_CONTROL_INSPECTION_TIMEOUT_MS = 1_000
_REPLY_CONTROL_SNAPSHOT_MAX_ATTEMPTS = 3
# Read the DOM quickly after a click, then use bounded, lower-frequency polls
# if Studio renders the replies asynchronously.
_REPLY_EXPANSION_POST_CLICK_WAIT_MS = 250
_REPLY_EXPANSION_MAX_CLICKS = 1_000
_REPLY_EXPANSION_MAX_ATTEMPTS = 3
_REPLY_TOTAL_CLICK_ATTEMPTS_KEY = object()
_PUBLIC_COMMENT_WAIT_MS = 750
_PUBLIC_COMMENT_POLL_LIMIT = 40
_PUBLIC_PREFLIGHT_POLL_LIMIT = _PUBLIC_COMMENT_POLL_LIMIT
_PUBLIC_NAVIGATION_TIMEOUT_MS = 90_000
_PUBLIC_NAVIGATION_ATTEMPTS = 2
_PUBLIC_MAX_LOAD_MORE_CLICKS = 30
_PUBLIC_VERIFICATION_TIMEOUT_MS = 20 * 60_000
_STUDIO_CONFIRM_DELAY_MS = 2_000
_SUBMIT_TRACE_LIMIT = 5

_SAFE_LOOKUP_EXCEPTION_DETAIL_RE = re.compile(
    r"\b((?:(?:browser|studio|page|locator|selector|source|comment|element|"
    r"target|execution|network|request|response|context)\s+){0,3}"
    r"(?:lookup|search|scroll|navigation|connection|operation|evaluation|"
    r"query|request|response|context|page|element|selector)\s+"
    r"(?:failed|timed\s+out|closed|detached|destroyed|missing|unavailable|"
    r"not\s+found|invalid|rejected|aborted|exceeded|error))\b",
    re.IGNORECASE,
)
_SAFE_BROWSER_NETWORK_ERRORS = frozenset(
    {
        "ERR_ABORTED",
        "ERR_BLOCKED_BY_CLIENT",
        "ERR_CERT_AUTHORITY_INVALID",
        "ERR_CERT_COMMON_NAME_INVALID",
        "ERR_CONNECTION_CLOSED",
        "ERR_CONNECTION_REFUSED",
        "ERR_CONNECTION_RESET",
        "ERR_INTERNET_DISCONNECTED",
        "ERR_NAME_NOT_RESOLVED",
        "ERR_NETWORK_CHANGED",
        "ERR_PROXY_CONNECTION_FAILED",
        "ERR_SSL_PROTOCOL_ERROR",
        "ERR_TIMED_OUT",
        "ERR_TUNNEL_CONNECTION_FAILED",
    }
)
_SAFE_BROWSER_NETWORK_ERROR_RE = re.compile(r"\bnet::(ERR_[A-Z0-9_]+)\b", re.IGNORECASE)
_SAFE_BROWSER_TARGET_CLOSED_RE = re.compile(
    r"\b(?:target page,\s*context\s+or\s+browser|target page|browser context|"
    r"target|browser|context|page)(?:\s+(?:has been|was|is))?\s+closed\b",
    re.IGNORECASE,
)
_SAFE_BROWSER_FRAME_DETACHED_RE = re.compile(
    r"\b(?:frame\s+(?:(?:was|has been|is)\s+)?detached|detached\s+frame)\b",
    re.IGNORECASE,
)
_SAFE_BROWSER_EXECUTION_CONTEXT_DESTROYED_RE = re.compile(
    r"\bexecution\s+context\s+(?:(?:was|has been|is)\s+)?destroyed\b",
    re.IGNORECASE,
)


def _safe_browser_exception_cause(message: str) -> str | None:
    network_error = _SAFE_BROWSER_NETWORK_ERROR_RE.search(message)
    if network_error:
        code = network_error.group(1).upper()
        if code in _SAFE_BROWSER_NETWORK_ERRORS:
            return f"browser network error: net::{code}"
    if _SAFE_BROWSER_TARGET_CLOSED_RE.search(message):
        return "browser target was closed"
    if _SAFE_BROWSER_FRAME_DETACHED_RE.search(message):
        return "browser frame was detached"
    if _SAFE_BROWSER_EXECUTION_CONTEXT_DESTROYED_RE.search(message):
        return "browser execution context was destroyed"
    return None


def _safe_exception_fields(exc: Exception) -> dict[str, str]:
    """Return exception diagnostics without copying its potentially sensitive text."""
    exception_name = type(exc).__name__
    try:
        message = str(exc).casefold()
    except Exception:
        message = ""
    safe_cause = _safe_browser_exception_cause(message)
    if safe_cause:
        description = safe_cause
    elif "timeout" in exception_name.casefold() or "timeout" in message:
        description = "browser operation timed out"
    elif "navigation" in message:
        description = "browser navigation failed"
    elif "connection" in message:
        description = "browser connection failed"
    else:
        description = "browser operation failed"
    return {
        "failure_type": exception_name,
        "failure_description": description,
    }


def _safe_lookup_exception_fields(exc: Exception) -> dict[str, str]:
    fields = _safe_exception_fields(exc)
    if fields["failure_description"] != "browser operation failed":
        return fields
    try:
        message = str(exc)
    except Exception:
        return fields
    # Only retain a recognized browser diagnostic phrase. This excludes arbitrary
    # exception text while still providing a useful cause when one is present.
    message = re.sub(r"https?://\S+", " ", message, flags=re.IGNORECASE)
    match = _SAFE_LOOKUP_EXCEPTION_DETAIL_RE.search(message)
    if match:
        fields["failure_description"] = " ".join(match.group(1).casefold().split())
    return fields


def _response_status(response: Any) -> int | None:
    if response is None:
        return None
    try:
        return int(response.status)
    except (AttributeError, TypeError, ValueError):
        return None


def _publication_failure_location(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, PublicationUnconfirmedError) and isinstance(exc.__cause__, Exception):
        return _publication_failure_location(exc.__cause__)
    if isinstance(exc, SourceCommentUnavailableError):
        return "studio_source_comment_search", "source_comment_not_found"
    try:
        message = str(exc).casefold()
    except Exception:
        message = ""
    if "public article navigation failed" in message:
        return "public_article_navigation", "navigation_exception"
    if "public article comments did not load" in message:
        return "public_comment_loading", "comments_not_loaded"
    if "public article verification time limit reached" in message:
        return "public_reply_confirmation", "verification_timeout"
    if "public article comments did not expand" in message:
        return "public_comment_loading", "additional_comments_not_loaded"
    if "public article replies did not expand" in message:
        return "public_thread_expansion", "replies_not_expanded"
    if "source comment not found in public article" in message:
        return "public_source_comment_search", "source_comment_not_found"
    if "not confirmed in public article" in message:
        return "public_reply_confirmation", "reply_not_confirmed"
    if "not confirmed in studio" in message:
        return "studio_reply_confirmation", "reply_not_confirmed"
    if "send button was not found" in message:
        return "publication_controls", "send_button_not_found"
    if "send button remained visible" in message:
        return "publication_submit", "send_button_still_visible"
    return "publication_action", "operation_failed"


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


def _post_href_from_locator(group, *, timeout_ms: int | None = None) -> str:
    for selector in (selectors.POST_LINK, selectors.POST_LINK_FALLBACK):
        links = group.locator(selector)
        for index in range(links.count()):
            link = links.nth(index)
            if timeout_ms is not None:
                href = link.get_attribute("href", timeout=timeout_ms) or ""
            else:
                href = link.get_attribute("href") or ""
            if _post_url(href) is not None:
                return href
    return ""


def parse_relative_time(text: str | None, now: datetime) -> datetime | None:
    if not text:
        return None
    match = _MINUTES_RE.search(text)
    if not match:
        return None
    return now - timedelta(minutes=int(match.group(1)))


class StudioFeedScanIncompleteError(RuntimeError):
    """The Studio feed did not stabilize within the bounded scan passes."""


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
        self._reply_expansion_phase = "not_started"

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
        text = ""
        navigation_failed = False
        try:
            response = article_page.goto(post_url, wait_until="domcontentloaded")
            http_status = _response_status(response)
            if http_status is not None and http_status >= 400:
                logger.info(
                    "Dzen article navigation returned an unsuccessful HTTP status",
                    extra={
                        "event": "article_navigation_failed",
                        "failure_stage": "article_navigation",
                        "failure_reason": "http_status",
                        "failure_type": "HTTPStatus",
                        "failure_description": "navigation returned an unsuccessful HTTP status",
                        "http_status": http_status,
                        "navigation_result": "http_error",
                    },
                )
            else:
                logger.info(
                    "Dzen article navigation completed",
                    extra={
                        "event": "article_navigation_completed",
                        "http_status": http_status,
                        "navigation_result": "completed",
                    },
                )
            try:
                text = self._extract_article_text(article_page)
            except Exception as exc:
                logger.info(
                    "Failed to extract Dzen article text",
                    extra={
                        "event": "article_text_fetch_failed",
                        "failure_stage": "article_text_extraction",
                        "failure_reason": "article_text_extraction_failed",
                        **_safe_exception_fields(exc),
                        "http_status": http_status,
                        "article_text_length": 0,
                    },
                )
        except Exception as exc:
            navigation_failed = True
            logger.info(
                "Failed to navigate to Dzen article",
                extra={
                    "event": "article_text_fetch_failed",
                    "failure_stage": "article_navigation",
                    "failure_reason": "navigation_exception",
                    **_safe_exception_fields(exc),
                    "navigation_result": "exception",
                    "article_text_length": 0,
                },
            )
        finally:
            article_page.close()
        if not text and not navigation_failed:
            logger.info(
                "Dzen article text was not found",
                extra={
                    "event": "article_text_fetch_failed",
                    "failure_stage": "article_text_extraction",
                    "failure_reason": "article_text_not_found",
                    "failure_type": "ContentNotFound",
                    "failure_description": "article text was empty or unavailable",
                    "article_text_length": len(text),
                },
            )
        elif text:
            logger.info(
                "Dzen article text extracted",
                extra={
                    "event": "article_text_fetch_completed",
                    "article_text_length": len(text),
                },
            )
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
        skipped_missing_link_count = 0
        scan_pass_count = 0
        stable_pass_count = 0
        scan_complete = False
        reply_expansion_keys: set[Any] = set()
        reply_expansion_attempts: dict[Any, int] = {}
        reply_expansion_deadline: float | None = None
        failure_phase = "initial_feed_snapshot"
        try:
            groups, previous_counts = self._studio_feed_snapshot()
            for scan_pass_count in range(1, _STUDIO_FEED_MAX_SCAN_PASSES + 1):
                failure_phase = "scroll_to_last_loaded_item"
                self._scroll_to_last_loaded_item(groups)
                failure_phase = "reply_control_detection"
                if (
                    reply_expansion_deadline is None
                    and self._page.query_selector_all(selectors.COMMENT_OPEN_MORE)
                ):
                    reply_expansion_deadline = (
                        monotonic() + _REPLY_EXPANSION_OPERATION_TIMEOUT_MS / 1_000
                    )
                failure_phase = "reply_control_group_detection"
                for group_index in self._groups_with_reply_controls(groups):
                    failure_phase = "reply_group_identity"
                    group = groups[group_index]
                    expected_post_href = _post_href(group)
                    page_locator = getattr(self._page, "locator", None)
                    if callable(page_locator):
                        scope = page_locator(selectors.POST_GROUP).nth(group_index)
                        initial_controls = None
                    else:
                        scope = group
                        initial_controls = group.query_selector_all(
                            selectors.COMMENT_OPEN_MORE
                        )
                        if not initial_controls:
                            continue
                    failure_phase = "reply_expansion"
                    self._expand_hidden_replies(
                        scope=scope,
                        initial_controls=initial_controls,
                        clicked_keys=reply_expansion_keys,
                        attempt_counts=reply_expansion_attempts,
                        deadline=reply_expansion_deadline,
                        expected_post_href=expected_post_href or None,
                        scope_index=group_index,
                    )
                failure_phase = "feed_snapshot"
                groups, current_counts = self._studio_feed_snapshot()
                if current_counts == previous_counts:
                    stable_pass_count += 1
                else:
                    stable_pass_count = 0
                previous_counts = current_counts
                if stable_pass_count == _STUDIO_FEED_STABLE_PASSES:
                    scan_complete = True
                    break

            failure_phase = "comment_extraction"
            for card_index, group in enumerate(groups, start=1):
                post_href = _post_href(group)
                if not post_href:
                    # Keep the existing skip behavior: an unresolved link would
                    # create an unstable synthetic comment id.
                    skipped_missing_link_count += 1
                    logger.info(
                        "Skipped Dzen publication card without a recognized link",
                        extra={
                            "event": "studio_publication_skipped",
                            "failure_stage": "studio_publication_link",
                            "failure_reason": "post_link_not_found",
                            "publication_card_index": card_index,
                        },
                    )
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
        except Exception as exc:
            if failure_phase == "reply_expansion":
                failure_phase = (
                    f"reply_expansion_{self._reply_expansion_phase}"
                )
            logger.info(
                "Failed to read Dzen Studio feed and comments",
                extra={
                    "event": "studio_comments_read_failed",
                    "failure_stage": "studio_feed_read",
                    "failure_reason": "page_read_failed",
                    "failure_phase": failure_phase,
                    **_safe_exception_fields(exc),
                    "publication_card_count": len(groups) if "groups" in locals() else 0,
                    "comments_extracted": len(comments),
                    "skipped_missing_link_count": skipped_missing_link_count,
                    "scan_pass_count": scan_pass_count,
                },
            )
            raise
        if scan_complete:
            logger.info(
                "Read Dzen Studio feed and comments",
                extra={
                    "event": "studio_comments_read_completed",
                    "publication_card_count": len(groups),
                    "comments_extracted": len(comments),
                    "skipped_missing_link_count": skipped_missing_link_count,
                    "scan_pass_count": scan_pass_count,
                    "stable_pass_count": stable_pass_count,
                },
            )
        else:
            logger.info(
                "Dzen Studio feed scan reached its pass limit before stabilizing",
                extra={
                    "event": "studio_comments_read_incomplete",
                    "failure_stage": "studio_feed_read",
                    "failure_reason": "stability_pass_limit_reached",
                    "publication_card_count": len(groups),
                    "comments_extracted": len(comments),
                    "skipped_missing_link_count": skipped_missing_link_count,
                    "scan_pass_count": scan_pass_count,
                    "stable_pass_count": stable_pass_count,
                },
            )
            raise StudioFeedScanIncompleteError(
                "Studio feed scan reached its pass limit "
                f"after {scan_pass_count} passes"
            )
        return comments

    def _studio_feed_snapshot(self):
        groups = self._page.query_selector_all(selectors.POST_GROUP)
        page_locator = getattr(self._page, "locator", None)
        evaluate = getattr(self._page, "evaluate", None)
        if callable(page_locator) and callable(evaluate):
            counts = evaluate(
                _STUDIO_FEED_COUNTS_SCRIPT,
                {
                    "group": selectors.POST_GROUP,
                    "comment": selectors.COMMENT_NODE,
                    "more": selectors.COMMENT_OPEN_MORE,
                },
            )
            if isinstance(counts, dict) and counts.get("group_count") == len(groups):
                return groups, (
                    counts["group_count"],
                    counts["comment_count"],
                    counts["button_count"],
                )
        comment_count = sum(
            len(group.query_selector_all(selectors.COMMENT_NODE)) for group in groups
        )
        button_count = len(self._page.query_selector_all(selectors.COMMENT_OPEN_MORE))
        return groups, (len(groups), comment_count, button_count)

    def _groups_with_reply_controls(self, groups) -> list[int]:
        evaluate = getattr(self._page, "evaluate", None)
        if callable(getattr(self._page, "locator", None)) and callable(evaluate):
            indices = evaluate(
                _STUDIO_REPLY_GROUP_INDICES_SCRIPT,
                {
                    "group": selectors.POST_GROUP,
                    "more": selectors.COMMENT_OPEN_MORE,
                },
            )
            if isinstance(indices, list) and all(
                isinstance(index, int) and 0 <= index < len(groups)
                for index in indices
            ):
                return indices
        return [
            index
            for index, group in enumerate(groups)
            if group.query_selector_all(selectors.COMMENT_OPEN_MORE)
        ]

    def _scroll_to_last_loaded_item(self, groups) -> None:
        evaluate = getattr(self._page, "evaluate", None)
        if callable(getattr(self._page, "locator", None)) and callable(evaluate):
            evaluate(
                _STUDIO_FEED_SCROLL_SCRIPT,
                {"group": selectors.POST_GROUP, "comment": selectors.COMMENT_NODE},
            )
            self._page.mouse.wheel(0, _REPLY_SEARCH_SCROLL_DELTA_Y)
            self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
            return

        last_item = groups[-1] if groups else None
        for group in reversed(groups):
            comments = group.query_selector_all(selectors.COMMENT_NODE)
            if comments:
                last_item = comments[-1]
                break
        if last_item is not None:
            last_item.scroll_into_view_if_needed()
        self._page.mouse.wheel(0, _REPLY_SEARCH_SCROLL_DELTA_Y)
        self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)

    def _expand_hidden_replies(
        self,
        *,
        scope: Any | None = None,
        initial_controls: list[Any] | None = None,
        clicked_keys: set[Any] | None = None,
        attempt_counts: dict[Any, int] | None = None,
        deadline: float | None = None,
        expected_post_href: str | None = None,
        scope_index: int | None = None,
    ) -> int:
        if clicked_keys is None:
            clicked_keys = set()
        if attempt_counts is None:
            attempt_counts = {}
        clicked_count = 0
        first_controls = initial_controls
        unresolved_click_keys: set[str] = set()
        control_info_by_key: dict[str, dict[str, Any]] = {}

        def read_controls_once() -> tuple[list[tuple[Any, str]], set[str]]:
            nonlocal first_controls
            control_metadata: list[dict[str, Any]] | None = None
            self._reply_expansion_phase = "control_query"
            if first_controls is not None:
                buttons = first_controls
                first_controls = None
            else:
                page_locator = getattr(self._page, "locator", None)
                page_evaluate = getattr(self._page, "evaluate", None)
                can_read_dom_snapshot = (
                    callable(page_locator)
                    and callable(page_evaluate)
                    and (scope_index is not None or scope is None)
                )
                if can_read_dom_snapshot:
                    self._reply_expansion_phase = "control_dom_snapshot"
                    snapshot = page_evaluate(
                        _REPLY_CONTROL_SNAPSHOT_SCRIPT,
                        {
                            "group": selectors.POST_GROUP,
                            "more": selectors.COMMENT_OPEN_MORE,
                            "comment": selectors.COMMENT_NODE,
                            "postLink": selectors.POST_LINK,
                            "postLinkFallback": selectors.POST_LINK_FALLBACK,
                            "scopeIndex": scope_index,
                            "expectedPostHref": expected_post_href,
                        },
                    )
                    if not isinstance(snapshot, dict) or not isinstance(
                        snapshot.get("controls"), list
                    ):
                        raise RuntimeError(
                            "reply expansion could not read a control snapshot"
                        )
                    if snapshot.get("identity_matches") is not True:
                        raise RuntimeError(
                            "Studio publication changed during reply expansion"
                        )
                    control_metadata = snapshot["controls"]
                    if scope_index is not None:
                        button_locator = page_locator(selectors.POST_GROUP).nth(
                            scope_index
                        ).locator(selectors.COMMENT_OPEN_MORE)
                        buttons = [
                            button_locator.nth(item["button_index"])
                            for item in control_metadata
                        ]
                    else:
                        button_locator = page_locator(selectors.COMMENT_OPEN_MORE)
                        if any(item["global_index"] < 0 for item in control_metadata):
                            raise RuntimeError(
                                "reply expansion found a control outside its comment group"
                            )
                        buttons = [
                            button_locator.nth(item["global_index"])
                            for item in control_metadata
                        ]
                else:
                    control_scope = scope if scope is not None else self._page
                    locator = getattr(control_scope, "locator", None)
                    if callable(locator):
                        if (
                            scope is not None
                            and expected_post_href
                            and _post_href_from_locator(
                                control_scope,
                                timeout_ms=_REPLY_CONTROL_INSPECTION_TIMEOUT_MS,
                            )
                            != expected_post_href
                        ):
                            raise RuntimeError(
                                "Studio publication changed during reply expansion"
                            )
                        button_locator = locator(selectors.COMMENT_OPEN_MORE)
                        buttons = [
                            button_locator.nth(index)
                            for index in range(button_locator.count())
                        ]
                    else:
                        buttons = control_scope.query_selector_all(
                            selectors.COMMENT_OPEN_MORE
                        )
            if control_metadata is not None:
                visibility = [bool(item["visible"]) for item in control_metadata]
            else:
                visibility = []
                self._reply_expansion_phase = "control_visibility"
                for button in buttons:
                    is_visible = getattr(button, "is_visible", None)
                    if not callable(is_visible):
                        visibility.append(True)
                        continue
                    try:
                        visibility.append(bool(is_visible()))
                    except Exception as exc:
                        self._log_reply_expansion_incomplete(
                            failure_reason="reply_control_visibility_unavailable",
                            clicked_count=clicked_count,
                            visible_button_count=sum(visibility),
                        )
                        raise RuntimeError(
                            "reply expansion stopped because a control's visibility "
                            "could not be determined"
                        ) from exc

            controls: list[tuple[Any, str]] = []
            present_keys: set[str] = set()
            self._reply_expansion_phase = "control_identity"
            for button_index, (button, is_visible) in enumerate(
                zip(buttons, visibility, strict=True)
            ):
                metadata = (
                    control_metadata[button_index]
                    if control_metadata is not None
                    else None
                )
                if metadata is not None:
                    button_key = metadata.get("key")
                elif callable(getattr(button, "count", None)):
                    button_key = button.evaluate(
                        _REPLY_BUTTON_KEY_SCRIPT,
                        timeout=_REPLY_CONTROL_INSPECTION_TIMEOUT_MS,
                    )
                else:
                    button_key = button.evaluate(_REPLY_BUTTON_KEY_SCRIPT)
                if not isinstance(button_key, str) or not button_key:
                    try:
                        if metadata is not None:
                            control_class = metadata.get("class_name", "")
                            control_text = metadata.get("text", "")
                        elif callable(getattr(button, "count", None)):
                            control_class = (
                                button.get_attribute(
                                    "class",
                                    timeout=_REPLY_CONTROL_INSPECTION_TIMEOUT_MS,
                                )
                                or ""
                            )
                        else:
                            control_class = button.get_attribute("class") or ""
                        control_text = button.inner_text().strip()
                    except Exception:
                        control_class = ""
                        control_text = ""
                    if expected_post_href:
                        scope_key = expected_post_href
                    elif metadata is not None:
                        scope_key = f"group:{metadata['group_index']}"
                    elif scope_index is not None:
                        scope_key = f"group:{scope_index}"
                    else:
                        scope_key = "page"
                    button_key = "fallback:" + json.dumps(
                        [
                            scope_key,
                            metadata.get("button_index", button_index)
                            if metadata is not None
                            else button_index,
                            control_class,
                            control_text,
                        ],
                        ensure_ascii=False,
                    )
                present_keys.add(button_key)
                control_info_by_key[button_key] = metadata or {}
                if is_visible:
                    controls.append((button, button_key))
            return controls, present_keys

        def read_controls() -> tuple[list[tuple[Any, str]], set[str]]:
            for snapshot_attempt in range(
                1, _REPLY_CONTROL_SNAPSHOT_MAX_ATTEMPTS + 1
            ):
                try:
                    return read_controls_once()
                except Exception as exc:
                    exception_name = type(exc).__name__.casefold()
                    exception_message = str(exc).casefold()
                    is_timeout = (
                        "timeout" in exception_name
                        or "timeout" in exception_message
                    )
                    if (
                        not is_timeout
                        or snapshot_attempt == _REPLY_CONTROL_SNAPSHOT_MAX_ATTEMPTS
                    ):
                        raise
                    logger.info(
                        "Dzen reply controls will be re-read after a DOM update",
                        extra={
                            "event": "studio_reply_expansion_control_snapshot_deferred",
                            "failure_stage": "studio_reply_expansion",
                            "failure_reason": "control_snapshot_timeout",
                            "snapshot_attempt": snapshot_attempt,
                        },
                    )
                    self._page.wait_for_timeout(
                        _REPLY_EXPANSION_POST_CLICK_WAIT_MS
                    )

        def wait_for_click_result(
            button_key: str,
            before: dict[str, Any],
            previous_control_count: int,
        ) -> tuple[list[tuple[Any, str]], set[str]]:
            operation_deadline = deadline or (
                monotonic() + _REPLY_EXPANSION_OPERATION_TIMEOUT_MS / 1_000
            )
            render_deadline = min(
                operation_deadline,
                monotonic() + _REPLY_EXPANSION_RENDER_TIMEOUT_MS / 1_000,
            )
            first_poll = True
            while True:
                remaining_ms = int((render_deadline - monotonic()) * 1_000)
                if remaining_ms <= 0:
                    return read_controls()
                self._reply_expansion_phase = "post_click_settle"
                self._page.wait_for_timeout(
                    min(
                        _REPLY_EXPANSION_POST_CLICK_WAIT_MS
                        if first_poll
                        else _REPLY_EXPANSION_RENDER_POLL_MS,
                        remaining_ms,
                    )
                )
                first_poll = False
                current_controls, current_keys = read_controls()
                if button_key not in current_keys:
                    return current_controls, current_keys
                if (
                    button_key.startswith("fallback:")
                    and len(current_keys) < previous_control_count
                ):
                    return current_controls, current_keys
                if self._reply_control_revealed_comments(
                    before, control_info_by_key.get(button_key, {})
                ):
                    return current_controls, current_keys

        controls, present_keys = read_controls()
        while True:
            pending_controls = [
                (button, button_key)
                for button, button_key in controls
                if (button_key.startswith("fallback:") or button_key not in clicked_keys)
                and attempt_counts.get(button_key, 0) < _REPLY_EXPANSION_MAX_ATTEMPTS
            ]
            if not pending_controls:
                visible_keys = {key for _, key in controls}
                hidden_pending_keys = {
                    key
                    for key in present_keys.difference(visible_keys)
                    if (key.startswith("fallback:") or key not in clicked_keys)
                    and attempt_counts.get(key, 0) < _REPLY_EXPANSION_MAX_ATTEMPTS
                }
                if hidden_pending_keys:
                    self._log_reply_expansion_incomplete(
                        failure_reason="reply_control_not_visible",
                        clicked_count=clicked_count,
                        visible_button_count=len(controls),
                    )
                    raise RuntimeError(
                        "reply expansion stopped with controls hidden inside "
                        "a collapsed thread"
                    )
                if unresolved_click_keys:
                    self._log_reply_expansion_incomplete(
                        failure_reason="click_target_not_visible_after_retry",
                        clicked_count=clicked_count,
                        visible_button_count=len(controls),
                    )
                    raise RuntimeError(
                        "reply expansion could not reacquire a control after a "
                        "not-visible click failure"
                    )
                return clicked_count

            # Expand newly revealed nested controls before spending another
            # attempt on a control that remained visible after its click.
            next_button, next_button_key = next(
                (
                    control
                    for control in pending_controls
                    if attempt_counts.get(control[1], 0) == 0
                ),
                pending_controls[0],
            )

            total_click_attempts = attempt_counts.get(
                _REPLY_TOTAL_CLICK_ATTEMPTS_KEY, 0
            )
            if total_click_attempts >= _REPLY_EXPANSION_MAX_CLICKS:
                self._log_reply_expansion_incomplete(
                    failure_reason="click_limit_reached",
                    clicked_count=clicked_count,
                    visible_button_count=len(controls),
                )
                raise RuntimeError(
                    "reply expansion reached its click limit before all controls expanded"
                )

            if deadline is None:
                deadline = monotonic() + _REPLY_EXPANSION_OPERATION_TIMEOUT_MS / 1_000
            remaining_ms = int((deadline - monotonic()) * 1_000)
            if remaining_ms <= 0:
                self._log_reply_expansion_incomplete(
                    failure_reason="time_limit_reached",
                    clicked_count=clicked_count,
                    visible_button_count=len(controls),
                )
                raise RuntimeError(
                    "reply expansion reached its time limit before all controls expanded"
                )

            attempt_counts[_REPLY_TOTAL_CLICK_ATTEMPTS_KEY] = (
                total_click_attempts + 1
            )
            attempt_counts[next_button_key] = attempt_counts.get(
                next_button_key, 0
            ) + 1
            click_timeout_ms = min(_REPLY_EXPANSION_CLICK_TIMEOUT_MS, remaining_ms)
            previous_control_count = len(present_keys)
            previous_control_info = control_info_by_key.get(next_button_key, {})
            try:
                self._reply_expansion_phase = "control_click"
                if (
                    previous_control_info
                    and callable(getattr(self._page, "evaluate", None))
                ):
                    clicked = self._page.evaluate(
                        _REPLY_CONTROL_CLICK_SCRIPT,
                        {
                            "group": selectors.POST_GROUP,
                            "more": selectors.COMMENT_OPEN_MORE,
                            "postLink": selectors.POST_LINK,
                            "postLinkFallback": selectors.POST_LINK_FALLBACK,
                            "groupIndex": previous_control_info["group_index"],
                            "buttonIndex": previous_control_info["button_index"],
                            "expectedPostHref": previous_control_info["post_href"],
                            "expectedKey": previous_control_info.get("key"),
                            "expectedClass": previous_control_info["class_name"],
                            "expectedText": previous_control_info["text"],
                        },
                    )
                    if clicked is not True:
                        raise TimeoutError(
                            "reply control changed before it could be clicked"
                        )
                else:
                    next_button.click(force=True, timeout=click_timeout_ms)
            except Exception as exc:
                exception_name = type(exc).__name__.casefold()
                exception_message = str(exc).casefold()
                is_not_visible = "not visible" in exception_message
                is_timeout = "timeout" in exception_name or "timeout" in exception_message
                if (
                    (is_not_visible or is_timeout)
                    and attempt_counts[next_button_key]
                    < _REPLY_EXPANSION_MAX_ATTEMPTS
                ):
                    unresolved_click_keys.add(next_button_key)
                    logger.info(
                        "Dzen reply control will be reacquired after a visibility race",
                        extra={
                            "event": "studio_reply_expansion_click_deferred",
                            "failure_stage": "studio_reply_expansion",
                            "failure_reason": (
                                "control_not_visible"
                                if is_not_visible
                                else "control_click_timeout"
                            ),
                            "click_attempt": attempt_counts[next_button_key],
                            "clicked_count": clicked_count,
                            "visible_button_count": len(controls),
                        },
                    )
                    remaining_ms = int((deadline - monotonic()) * 1_000)
                    if remaining_ms > 0:
                        self._reply_expansion_phase = "click_timeout_settle"
                        self._page.wait_for_timeout(
                            min(_REPLY_EXPANSION_POST_CLICK_WAIT_MS, remaining_ms)
                        )
                    controls, present_keys = wait_for_click_result(
                        next_button_key,
                        previous_control_info,
                        previous_control_count,
                    )
                    if (
                        next_button_key not in present_keys
                        or (
                            next_button_key.startswith("fallback:")
                            and len(present_keys) < previous_control_count
                        )
                    ):
                        unresolved_click_keys.discard(next_button_key)
                        if next_button_key.startswith("fallback:"):
                            attempt_counts.pop(next_button_key, None)
                        else:
                            clicked_keys.add(next_button_key)
                        clicked_count += 1
                    elif self._reply_control_revealed_comments(
                        previous_control_info,
                        control_info_by_key.get(next_button_key, {}),
                    ):
                        unresolved_click_keys.discard(next_button_key)
                        if next_button_key.startswith("fallback:"):
                            attempt_counts[next_button_key] = (
                                _REPLY_EXPANSION_MAX_ATTEMPTS
                            )
                        else:
                            clicked_keys.add(next_button_key)
                        clicked_count += 1
                    continue
                self._log_reply_expansion_incomplete(
                    failure_reason=(
                        "click_timeout"
                        if "timeout" in type(exc).__name__.casefold()
                        else "click_failed"
                    ),
                    clicked_count=clicked_count,
                    visible_button_count=len(controls),
                )
                raise
            unresolved_click_keys.discard(next_button_key)
            clicked_count += 1
            controls, present_keys = wait_for_click_result(
                next_button_key,
                previous_control_info,
                previous_control_count,
            )
            if next_button_key not in present_keys:
                if next_button_key.startswith("fallback:"):
                    attempt_counts.pop(next_button_key, None)
                else:
                    clicked_keys.add(next_button_key)
            elif self._reply_control_revealed_comments(
                previous_control_info,
                control_info_by_key.get(next_button_key, {}),
            ):
                if next_button_key.startswith("fallback:"):
                    attempt_counts[next_button_key] = _REPLY_EXPANSION_MAX_ATTEMPTS
                else:
                    clicked_keys.add(next_button_key)
            elif (
                next_button_key.startswith("fallback:")
                and len(present_keys) < previous_control_count
            ):
                # A positional fallback key can shift when a preceding
                # ownerless control disappears; begin its new occupant fresh.
                attempt_counts.pop(next_button_key, None)
            elif attempt_counts[next_button_key] >= _REPLY_EXPANSION_MAX_ATTEMPTS:
                current_control_info = control_info_by_key.get(
                    next_button_key, {}
                )
                self._reply_expansion_phase = (
                    "control_still_present_after_successful_click"
                )
                self._log_reply_expansion_incomplete(
                    failure_reason="retry_limit_reached",
                    clicked_count=clicked_count,
                    visible_button_count=len(controls),
                    click_attempt=attempt_counts[next_button_key],
                    control_group_index=current_control_info.get("group_index"),
                    control_button_index=current_control_info.get("button_index"),
                    control_class=current_control_info.get("class_name"),
                    control_label=current_control_info.get("text"),
                    visible_comment_count_before=(
                        previous_control_info.get("group_comment_count")
                    ),
                    visible_comment_count_after=(
                        current_control_info.get("group_comment_count")
                    ),
                )
                raise RuntimeError(
                    "reply expansion failed because a control remained visible "
                    "after repeated clicks"
                )

    @staticmethod
    def _reply_control_revealed_comments(
        before: dict[str, Any], after: dict[str, Any]
    ) -> bool:
        before_count = before.get("group_comment_count")
        after_count = after.get("group_comment_count")
        if (
            not isinstance(before_count, int)
            or not isinstance(after_count, int)
            or after_count <= before_count
        ):
            return False

        label = " ".join(str(after.get("text", "")).replace("\u00a0", " ").split())
        if not label.casefold().startswith("показать"):
            return True
        match = re.match(r"показать\s+(\d+)\b", label, flags=re.IGNORECASE)
        return bool(match and after_count - before_count >= int(match.group(1)))

    @staticmethod
    def _log_reply_expansion_incomplete(
        *,
        failure_reason: str,
        clicked_count: int,
        visible_button_count: int,
        click_attempt: int | None = None,
        control_group_index: int | None = None,
        control_button_index: int | None = None,
        control_class: str | None = None,
        control_label: str | None = None,
        visible_comment_count_before: int | None = None,
        visible_comment_count_after: int | None = None,
    ) -> None:
        extra = {
            "event": "studio_reply_expansion_incomplete",
            "failure_stage": "studio_reply_expansion",
            "failure_reason": failure_reason,
            "clicked_count": clicked_count,
            "visible_button_count": visible_button_count,
        }
        optional_fields = {
            "click_attempt": click_attempt,
            "control_group_index": control_group_index,
            "control_button_index": control_button_index,
            "control_class": control_class,
            "control_label": control_label,
            "visible_comment_count_before": visible_comment_count_before,
            "visible_comment_count_after": visible_comment_count_after,
        }
        extra.update(
            {key: value for key, value in optional_fields.items() if value is not None}
        )
        logger.info(
            "Dzen hidden reply expansion stopped before all controls were expanded",
            extra=extra,
        )

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
        self,
        comment: Comment,
        text: str,
        *,
        auto_publish: bool,
        reply_id: int | None = None,
        before_submit: Callable[[], None] | None = None,
    ) -> None:
        correlation = {"reply_id": reply_id}
        logger.info(
            "Dzen publication action started",
            extra={
                "event": "publication_action_started",
                **correlation,
                "auto_publish": auto_publish,
            },
        )
        try:
            self._publish_reply(
                comment,
                text,
                auto_publish=auto_publish,
                reply_id=reply_id,
                before_submit=before_submit,
            )
        except Exception as exc:
            failure_stage, failure_reason = _publication_failure_location(exc)
            logger.info(
                "Dzen publication action failed",
                extra={
                    "event": "publication_action_failed",
                    **correlation,
                    "failure_stage": failure_stage,
                    "failure_reason": failure_reason,
                    **_safe_exception_fields(exc),
                },
            )
            raise

    def _publish_reply(
        self,
        comment: Comment,
        text: str,
        *,
        auto_publish: bool,
        reply_id: int | None,
        before_submit: Callable[[], None] | None,
    ) -> None:
        correlation = {"reply_id": reply_id}
        logger.info(
            "Searching for Dzen source comment",
            extra={
                "event": "publication_source_comment_search_started",
                **correlation,
            },
        )
        node = self._find_comment_node_with_scroll(
            comment.dzen_comment_id, reply_id=reply_id
        )
        if node is None:
            raise SourceCommentUnavailableError("source comment not found on Studio page")

        if auto_publish and self._has_published_reply(node, text):
            logger.info(
                "Matching Dzen reply is already visible",
                extra={
                    "event": "publication_reply_already_visible",
                    **correlation,
                },
            )
            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            source_author_href = author_link.get_attribute("href") or "" if author_link else ""
            logger.info(
                "Checking public article for existing Dzen reply",
                extra={"event": "publication_article_check_started", **correlation},
            )
            try:
                public_visible = self._verify_public_reply(
                    comment, text, source_author_href, comment.author, reply_id=reply_id
                )
            except Exception as exc:
                failure_stage, failure_reason = _publication_failure_location(exc)
                logger.info(
                    "Dzen public article verification failed",
                    extra={
                        "event": "publication_article_verification_failed",
                        **correlation,
                        "failure_stage": failure_stage,
                        "failure_reason": failure_reason,
                        **_safe_exception_fields(exc),
                    },
                )
                raise
            if not public_visible:
                logger.info(
                    "Existing Dzen reply was not confirmed in public article",
                    extra={
                        "event": "publication_article_verification_failed",
                        **correlation,
                        "failure_stage": "public_reply_confirmation",
                        "failure_reason": "reply_not_confirmed",
                        "failure_type": "ReplyNotConfirmed",
                        "failure_description": "reply was not visible in the public article",
                    },
                )
                raise RuntimeError("reply not confirmed in public article")
            logger.info(
                "Existing Dzen reply confirmed in Studio and public article",
                extra={"event": "publication_confirmed", **correlation},
            )
            return

        if auto_publish:
            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            source_author_href = author_link.get_attribute("href") or "" if author_link else ""
            logger.info(
                "Checking public article before Dzen reply submit",
                extra={"event": "publication_public_preflight_started", **correlation},
            )
            try:
                already_public = self._verify_public_reply(
                    comment, text, source_author_href, comment.author,
                    wait_for_reply=False,
                    reply_id=reply_id,
                )
            except Exception as exc:
                failure_stage, failure_reason = _publication_failure_location(exc)
                logger.info(
                    "Public article preflight failed before Dzen reply submit",
                    extra={
                        "event": "publication_public_preflight_failed",
                        **correlation,
                        "failure_stage": failure_stage,
                        "failure_reason": failure_reason,
                        **_safe_exception_fields(exc),
                    },
                )
                raise
            if already_public:
                logger.info(
                    "Existing Dzen reply found in public article before submit",
                    extra={"event": "publication_reply_already_public", **correlation},
                )
                logger.info(
                    "Existing Dzen reply confirmed in public article",
                    extra={"event": "publication_confirmed", **correlation},
                )
                return

        if not auto_publish:
            self._submit_reply(
                node,
                text,
                auto_publish=False,
                reply_id=reply_id,
            )
            logger.info(
                "Dzen reply draft prepared",
                extra={"event": "publication_draft_prepared", **correlation},
            )
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
        submit_attempted = False

        def mark_submit_attempted() -> None:
            nonlocal submit_attempted
            if before_submit is None:
                raise RuntimeError("durable publication submit marker is required")
            before_submit()
            submit_attempted = True

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            self._submit_reply(
                node,
                text,
                auto_publish=True,
                on_submit_attempt=mark_submit_attempted,
                reply_id=reply_id,
            )
            logger.info(
                "Waiting for Dzen publication acknowledgment",
                extra={
                    "event": "publication_acknowledgment_wait_started",
                    **correlation,
                },
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
            logger.info(
                "Dzen publication acknowledgment wait completed",
                extra={
                    "event": "publication_acknowledgment_wait_completed",
                    **correlation,
                    "acknowledged": acknowledged,
                    "response_outcome": creation_outcome,
                },
            )
            if creation_outcome == "non_2xx":
                raise RuntimeError("Dzen create response was non-2xx")

            page.wait_for_timeout(_STUDIO_CONFIRM_DELAY_MS)
            reply_visible = False
            for poll in range(_REPLY_SUBMIT_ACK_TIMEOUT_MS // _REPLY_SEARCH_WAIT_MS + 1):
                node, _ = self._find_comment_node(comment.dzen_comment_id)
                reply_visible = bool(node is not None and self._has_published_reply(node, text))
                logger.info(
                    "Checked Dzen Studio reply visibility",
                    extra={
                        "event": "publication_studio_visibility_check",
                        **correlation,
                        "poll": poll + 1,
                        "source_found": node is not None,
                        "reply_visible": reply_visible,
                    },
                )
                if reply_visible:
                    break
                page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)

            if not reply_visible:
                logger.info(
                    "Dzen reply was not confirmed in Studio",
                    extra={
                        "event": "publication_studio_verification_failed",
                        **correlation,
                        "failure_stage": "studio_reply_confirmation",
                        "failure_reason": "reply_not_confirmed",
                        "failure_type": "ReplyNotConfirmed",
                        "failure_description": "reply was not visible in Studio after waiting",
                        "poll_count": _REPLY_SUBMIT_ACK_TIMEOUT_MS // _REPLY_SEARCH_WAIT_MS + 1,
                        "source_found": node is not None,
                    },
                )
                outcomes = ", ".join(
                    f"{item['method']} {item['host']} "
                    f"path_sha256={item['path_sha256']} {item['status']}"
                    for item in mutations
                ) or "none"
                raise RuntimeError(
                    "reply not confirmed in Studio; "
                    f"creation_outcome={creation_outcome}; "
                    f"source_found={str(node is not None).lower()}; "
                    f"mutations=[{outcomes}]; "
                    f"truncated={str(truncated).lower()}"
                )

            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            source_author_href = author_link.get_attribute("href") or "" if author_link else ""
            logger.info(
                "Checking Dzen public article for published reply",
                extra={"event": "publication_article_check_started", **correlation},
            )
            try:
                public_visible = self._verify_public_reply(
                    comment, text, source_author_href, comment.author, reply_id=reply_id
                )
            except Exception as exc:
                failure_stage, failure_reason = _publication_failure_location(exc)
                logger.info(
                    "Dzen public article verification failed",
                    extra={
                        "event": "publication_article_verification_failed",
                        **correlation,
                        "failure_stage": failure_stage,
                        "failure_reason": failure_reason,
                        **_safe_exception_fields(exc),
                    },
                )
                raise
            if not public_visible:
                logger.info(
                    "Dzen reply was not confirmed in public article",
                    extra={
                        "event": "publication_article_verification_failed",
                        **correlation,
                        "failure_stage": "public_reply_confirmation",
                        "failure_reason": "reply_not_confirmed",
                        "failure_type": "ReplyNotConfirmed",
                        "failure_description": "reply was not visible in the public article",
                    },
                )
                raise RuntimeError("reply not confirmed in public article")
            logger.info(
                "Dzen reply confirmed in Studio and public article",
                extra={"event": "publication_confirmed", **correlation},
            )
        except Exception as exc:
            creation_outcome = self._creation_response_outcome(
                creation_response, creation_payload, normalized_text
            )
            if submit_attempted and creation_outcome != "non_2xx":
                failure_stage, failure_reason = _publication_failure_location(exc)
                raise PublicationUnconfirmedError(
                    "publication not confirmed after submit; "
                    f"failure_stage={failure_stage}; failure_reason={failure_reason}; "
                    f"creation_outcome={creation_outcome}"
                ) from exc
            raise
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

    def _verify_public_reply(
        self,
        comment: Comment,
        text: str,
        source_author_href: str = "",
        source_author: str = "",
        *,
        wait_for_reply: bool = True,
        reply_id: int | None = None,
    ) -> bool:
        """Confirm the reply inside its source comment on the public article."""
        post_url = _post_url(comment.post_url or "")
        if post_url is None:
            raise RuntimeError("public article verification requires an article URL")
        account_name = self._bot_account_name_provider()
        if not account_name.strip():
            raise RuntimeError("public article verification requires a bot author")

        deadline = monotonic() + _PUBLIC_VERIFICATION_TIMEOUT_MS / 1_000

        def ensure_time_budget() -> None:
            if monotonic() >= deadline:
                raise RuntimeError("public article verification time limit reached")

        article_page = self._page.context.new_page()
        navigation_status = None
        navigation_attempt_count = 0
        try:
            for attempt in range(1, _PUBLIC_NAVIGATION_ATTEMPTS + 1):
                ensure_time_budget()
                navigation_attempt_count = attempt
                try:
                    response = article_page.goto(
                        post_url,
                        wait_until="commit",
                        timeout=_PUBLIC_NAVIGATION_TIMEOUT_MS,
                    )
                except Exception as exc:
                    logger.info(
                        "Public article navigation attempt failed",
                        extra={
                            "event": "publication_article_navigation_retry",
                            "attempt": attempt,
                            "reply_id": reply_id,
                            "navigation_attempt_count": attempt,
                            "navigation_result": "exception",
                            "failure_stage": "public_article_navigation",
                            "failure_reason": "navigation_exception",
                            **_safe_exception_fields(exc),
                        },
                    )
                    if attempt == _PUBLIC_NAVIGATION_ATTEMPTS:
                        raise RuntimeError("public article navigation failed") from exc
                    article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                    continue
                navigation_status = _response_status(response)
                if navigation_status is not None and navigation_status >= 400:
                    logger.info(
                        "Public article navigation returned an unsuccessful HTTP status",
                        extra={
                            "event": "publication_article_navigation_failed",
                            "failure_stage": "public_article_navigation",
                            "failure_reason": "http_status",
                            "failure_type": "HTTPStatus",
                            "failure_description": "navigation returned an unsuccessful HTTP status",
                            "navigation_attempt_count": attempt,
                            "reply_id": reply_id,
                            "navigation_result": "http_error",
                            "http_status": navigation_status,
                        },
                    )
                else:
                    logger.info(
                        "Public article navigation completed",
                        extra={
                            "event": "publication_article_navigation_completed",
                            "navigation_attempt_count": attempt,
                            "reply_id": reply_id,
                            "navigation_result": "completed",
                            "http_status": navigation_status,
                        },
                    )
                break
            comment_wait_count = 0
            last_loading_exception = None
            for _ in range(_PUBLIC_COMMENT_POLL_LIMIT):
                ensure_time_budget()
                comment_wait_count += 1
                try:
                    article_page.evaluate(
                        "window.scrollTo(0, document.body?.scrollHeight || 0)"
                    )
                    comments = article_page.query_selector(selectors.ARTICLE_COMMENTS)
                    if comments is not None:
                        comments.scroll_into_view_if_needed()
                        break
                except Exception as exc:
                    last_loading_exception = exc
                    pass  # The document may still be loading after navigation commit.
                article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
            else:
                failure_details = (
                    _safe_exception_fields(last_loading_exception)
                    if last_loading_exception is not None
                    else {
                        "failure_type": "WaitTimeout",
                        "failure_description": "public comments did not load within the wait limit",
                    }
                )
                logger.info(
                    "Public article comments did not load",
                    extra={
                        "event": "publication_article_comments_failed",
                        "failure_stage": "public_comment_loading",
                        "failure_reason": "comments_not_loaded",
                        **failure_details,
                        "navigation_attempt_count": navigation_attempt_count,
                        "reply_id": reply_id,
                        "navigation_result": "http_error" if navigation_status is not None and navigation_status >= 400 else "completed",
                        "http_status": navigation_status,
                        "wait_count": comment_wait_count,
                    },
                )
                raise RuntimeError("public article comments did not load")
            logger.info(
                "Public article comments loaded",
                extra={
                    "event": "publication_article_comments_loaded",
                    "navigation_attempt_count": navigation_attempt_count,
                    "reply_id": reply_id,
                    "navigation_result": "http_error" if navigation_status is not None and navigation_status >= 400 else "completed",
                    "http_status": navigation_status,
                    "wait_count": comment_wait_count,
                },
            )

            sort_button = article_page.query_selector(selectors.ARTICLE_SORT)
            if sort_button is not None:
                try:
                    if "Сначала новые" not in sort_button.inner_text():
                        sort_button.click()
                        newest = None
                        for _ in range(6):
                            newest = article_page.query_selector(selectors.ARTICLE_SORT_NEWEST)
                            if newest is not None:
                                break
                            article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                        if newest is None:
                            sort_button.click()  # Close a menu without the expected option.
                        else:
                            newest.click()
                            article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                            logger.info(
                                "Sorted public article comments by newest",
                                extra={
                                    "event": "publication_article_sorted_newest",
                                    "navigation_attempt_count": navigation_attempt_count,
                                    "reply_id": reply_id,
                                },
                            )
                except Exception as exc:
                    logger.info(
                        "Public article sort option was unavailable",
                        extra={
                            "event": "publication_article_sort_unavailable",
                            "failure_stage": "public_comment_sort",
                            "failure_reason": "sort_unavailable",
                            "reply_id": reply_id,
                            **_safe_exception_fields(exc),
                        },
                    )

            expected_source_text = " ".join(comment.text.split())
            expected_reply_text = " ".join(text.split())
            source_href = urlsplit(source_author_href).path.rstrip("/")
            source_name = " ".join((source_author or comment.author).split()).casefold()
            load = 0
            load_more_click_attempt_count = 0
            empty_polls = 0
            roots_checked = 0
            candidates_checked = 0
            candidate_read_failure_count = 0
            branch_expansion_attempt_count = 0
            branch_expansion_click_count = 0
            branch_expansion_count = 0
            branch_expansion_wait_count = 0
            reply_confirmation_wait_count = 0
            while True:
                ensure_time_budget()
                roots = article_page.query_selector_all(selectors.ARTICLE_ROOT_COMMENT)
                roots_checked += len(roots)
                for index, root in enumerate(roots):
                    ensure_time_budget()
                    try:
                        data = self._read_public_comment(root)
                    except Exception:
                        candidate_read_failure_count += 1
                        continue  # React can replace a comment while it renders.
                    if data is None:
                        continue
                    candidates = [data, *data["replies"]]
                    candidates_checked += len(candidates)
                    source = next(
                        (
                            candidate for candidate in candidates
                            if self._public_source_matches(
                                candidate, expected_source_text, source_href, source_name
                            )
                        ),
                        None,
                    )
                    if source is None:
                        try:
                            expand = root.query_selector(selectors.ARTICLE_OPEN_REPLIES)
                            if expand is not None and "Свернуть" not in expand.inner_text():
                                previous_reply_count = len(data["replies"])
                                branch_expansion_attempt_count += 1
                                expand.click()
                                branch_expansion_click_count += 1
                                expansion_observed = False
                                for _ in range(_PUBLIC_COMMENT_POLL_LIMIT):
                                    branch_expansion_wait_count += 1
                                    article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                                    current_roots = article_page.query_selector_all(
                                        selectors.ARTICLE_ROOT_COMMENT
                                    )
                                    if index >= len(current_roots):
                                        continue
                                    data = self._read_public_comment(current_roots[index])
                                    if data is None:
                                        continue
                                    source = next(
                                        (
                                            candidate for candidate in [data, *data["replies"]]
                                            if self._public_source_matches(
                                                candidate, expected_source_text, source_href, source_name
                                            )
                                        ),
                                        None,
                                    )
                                    if source is not None or len(data["replies"]) > previous_reply_count:
                                        expansion_observed = True
                                        break
                                if expansion_observed:
                                    branch_expansion_count += 1
                                else:
                                    logger.info(
                                        "Public article comment branch did not expand",
                                        extra={
                                            "event": "publication_article_branch_expansion_failed",
                                            "reply_id": reply_id,
                                            "failure_stage": "public_thread_expansion",
                                            "failure_reason": "replies_not_expanded",
                                            "failure_type": "ExpansionTimeout",
                                            "failure_description": "reply count did not increase after expanding the branch",
                                            "branch_expansion_attempt_count": branch_expansion_attempt_count,
                                            "branch_expansion_click_count": branch_expansion_click_count,
                                            "branch_expansion_count": branch_expansion_count,
                                            "wait_count": _PUBLIC_COMMENT_POLL_LIMIT,
                                        },
                                    )
                        except Exception as exc:
                            logger.info(
                                "Public article comment branch expansion failed",
                                extra={
                                    "event": "publication_article_branch_expansion_failed",
                                    "reply_id": reply_id,
                                    "failure_stage": "public_thread_expansion",
                                    "failure_reason": "branch_expansion_exception",
                                    **_safe_exception_fields(exc),
                                    "branch_expansion_attempt_count": branch_expansion_attempt_count,
                                    "branch_expansion_click_count": branch_expansion_click_count,
                                    "branch_expansion_count": branch_expansion_count,
                                    "wait_count": branch_expansion_wait_count,
                                },
                            )
                            article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                    if source is None:
                        continue
                    logger.info(
                        "Found source comment in public article",
                        extra={
                            "event": "publication_article_source_found",
                            "reply_id": reply_id,
                            "load_more_click_count": load,
                            "roots_checked": roots_checked,
                            "candidates_checked": candidates_checked,
                            "branch_expansion_attempt_count": branch_expansion_attempt_count,
                            "branch_expansion_click_count": branch_expansion_click_count,
                            "branch_expansion_count": branch_expansion_count,
                            "source_kind": "root" if source is data else "child",
                        },
                    )
                    poll_limit = (
                        _PUBLIC_COMMENT_POLL_LIMIT
                        if wait_for_reply else _PUBLIC_PREFLIGHT_POLL_LIMIT
                    )
                    for poll in range(poll_limit + 1):
                        ensure_time_budget()
                        current_roots = article_page.query_selector_all(selectors.ARTICLE_ROOT_COMMENT)
                        if index >= len(current_roots):
                            if poll < poll_limit:
                                reply_confirmation_wait_count += 1
                                article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                            continue
                        root = current_roots[index]
                        try:
                            data = self._read_public_comment(root)
                        except Exception:
                            candidate_read_failure_count += 1
                            if poll < poll_limit:
                                reply_confirmation_wait_count += 1
                                article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                            continue
                        reply_candidates = data["replies"] if data is not None else []
                        candidates_checked += len(reply_candidates)
                        if data is not None and any(
                            is_bot_account_author(reply["author"], account_name)
                            and " ".join(reply["text"].split()) == expected_reply_text
                            and (
                                not reply.get("addressee")
                                or " ".join(reply["addressee"].split()).casefold()
                                == " ".join(source["author"].split()).casefold()
                            )
                            for reply in data["replies"]
                        ):
                            logger.info(
                                "Found matching child reply in public article",
                                extra={
                                    "event": "publication_article_reply_found",
                                    "reply_id": reply_id,
                                    "poll": poll + 1,
                                    "candidates_checked": candidates_checked,
                                    "reply_confirmation_wait_count": reply_confirmation_wait_count,
                                    "load_more_click_count": load,
                                },
                            )
                            return True
                        try:
                            expand = root.query_selector(selectors.ARTICLE_OPEN_REPLIES)
                            if expand is not None and "Свернуть" not in expand.inner_text():
                                branch_expansion_attempt_count += 1
                                expand.click()
                                branch_expansion_click_count += 1
                                branch_expansion_wait_count += 1
                        except Exception as exc:
                            logger.info(
                                "Public article reply branch expansion failed",
                                extra={
                                    "event": "publication_article_branch_expansion_failed",
                                    "reply_id": reply_id,
                                    "failure_stage": "public_thread_expansion",
                                    "failure_reason": "branch_expansion_exception",
                                    **_safe_exception_fields(exc),
                                    "branch_expansion_attempt_count": branch_expansion_attempt_count,
                                    "branch_expansion_click_count": branch_expansion_click_count,
                                    "branch_expansion_count": branch_expansion_count,
                                    "wait_count": branch_expansion_wait_count,
                                },
                            )
                            pass  # Re-query the comment after a transient DOM replacement.
                        if poll < poll_limit:
                            reply_confirmation_wait_count += 1
                            article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                    expand = root.query_selector(selectors.ARTICLE_OPEN_REPLIES)
                    if expand is not None and "Свернуть" not in expand.inner_text():
                        logger.info(
                            "Public article reply branch did not expand",
                            extra={
                                "event": "publication_article_branch_expansion_failed",
                                "reply_id": reply_id,
                                "failure_stage": "public_thread_expansion",
                                "failure_reason": "replies_not_expanded",
                                "failure_type": "ExpansionTimeout",
                                "failure_description": "reply branch remained collapsed after waiting",
                                "branch_expansion_attempt_count": branch_expansion_attempt_count,
                                "branch_expansion_click_count": branch_expansion_click_count,
                                "branch_expansion_count": branch_expansion_count,
                                "wait_count": reply_confirmation_wait_count,
                                "candidates_checked": candidates_checked,
                            },
                        )
                        raise RuntimeError("public article replies did not expand")
                    logger.info(
                        "Public article reply check completed",
                        extra={
                            "event": "publication_article_reply_check_completed",
                            "result": "not_found",
                            "reply_id": reply_id,
                            **(
                                {
                                    "failure_stage": "public_reply_confirmation",
                                    "failure_reason": "reply_not_confirmed",
                                    "failure_type": "ReplyNotConfirmed",
                                    "failure_description": "matching reply was not visible after waiting",
                                }
                                if wait_for_reply else {}
                            ),
                            "candidates_checked": candidates_checked,
                            "reply_confirmation_wait_count": reply_confirmation_wait_count,
                            "load_more_click_count": load,
                            "branch_expansion_attempt_count": branch_expansion_attempt_count,
                            "branch_expansion_click_count": branch_expansion_click_count,
                            "branch_expansion_count": branch_expansion_count,
                        },
                    )
                    return False

                more = article_page.query_selector(selectors.ARTICLE_MORE_COMMENTS)
                if more is None:
                    if roots:
                        break
                    empty_polls += 1
                    if empty_polls == _PUBLIC_COMMENT_POLL_LIMIT:
                        break
                    article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                    continue
                previous_count = len(roots)
                if load >= _PUBLIC_MAX_LOAD_MORE_CLICKS:
                    raise RuntimeError("public article source comment search limit reached")
                load_more_click_attempt_count += 1
                try:
                    more.click()
                except Exception as exc:
                    logger.info(
                        "Failed to click load-more comments control",
                        extra={
                            "event": "publication_article_more_comments_failed",
                            "reply_id": reply_id,
                            "failure_stage": "public_comment_loading",
                            "failure_reason": "load_more_click_failed",
                            **_safe_exception_fields(exc),
                            "load_more_click_count": load,
                            "load_more_click_attempt_count": load_more_click_attempt_count,
                            "roots_checked": roots_checked,
                        },
                    )
                    raise
                load += 1
                logger.info(
                    "Loaded more public article comments",
                    extra={
                        "event": "publication_article_more_comments_clicked",
                        "reply_id": reply_id,
                        "load_more_click_count": load,
                        "load_more_click_attempt_count": load_more_click_attempt_count,
                        "roots_checked": roots_checked,
                    },
                )
                load_wait_count = 0
                for _ in range(_PUBLIC_COMMENT_POLL_LIMIT):
                    ensure_time_budget()
                    load_wait_count += 1
                    article_page.wait_for_timeout(_PUBLIC_COMMENT_WAIT_MS)
                    if len(article_page.query_selector_all(selectors.ARTICLE_ROOT_COMMENT)) > previous_count:
                        empty_polls = 0
                        break
                else:
                    logger.info(
                        "Additional public article comments did not load",
                        extra={
                            "event": "publication_article_more_comments_failed",
                            "reply_id": reply_id,
                            "failure_stage": "public_comment_loading",
                            "failure_reason": "additional_comments_not_loaded",
                            "failure_type": "WaitTimeout",
                            "failure_description": "comment count did not increase after clicking load more",
                            "load_more_click_count": load,
                            "load_more_click_attempt_count": load_more_click_attempt_count,
                            "roots_checked": roots_checked,
                            "wait_count": load_wait_count,
                        },
                    )
                    raise RuntimeError("public article comments did not expand")
            logger.info(
                "Source comment was not found in public article",
                extra={
                    "event": "publication_article_source_missing",
                    "reply_id": reply_id,
                    "failure_stage": "public_source_comment_search",
                    "failure_reason": "source_comment_not_found",
                    "failure_type": "SourceCommentUnavailableError",
                    "failure_description": "source comment was not found after loading public comments",
                    "navigation_attempt_count": navigation_attempt_count,
                    "navigation_result": "http_error" if navigation_status is not None and navigation_status >= 400 else "completed",
                    "http_status": navigation_status,
                    "roots_checked": roots_checked,
                    "candidates_checked": candidates_checked,
                    "candidate_read_failure_count": candidate_read_failure_count,
                    "load_more_click_count": load,
                    "load_more_click_attempt_count": load_more_click_attempt_count,
                    "branch_expansion_attempt_count": branch_expansion_attempt_count,
                    "branch_expansion_click_count": branch_expansion_click_count,
                    "branch_expansion_count": branch_expansion_count,
                    "wait_count": empty_polls,
                },
            )
            raise RuntimeError("source comment not found in public article")
        finally:
            article_page.close()

    @staticmethod
    def _public_source_matches(
        candidate: dict[str, str], text: str, author_href: str, author_name: str
    ) -> bool:
        if " ".join(candidate["text"].split()) != text:
            return False
        candidate_href = urlsplit(candidate.get("authorHref", "")).path.rstrip("/")
        if author_href and candidate_href:
            if author_href == candidate_href:
                return True
            if author_href.split("/")[1:2] == candidate_href.split("/")[1:2]:
                return False
        return " ".join(candidate["author"].split()).casefold() == author_name

    @staticmethod
    def _read_public_comment(root: Any) -> dict[str, Any] | None:
        return root.evaluate(
            """(root) => {
                const own = root.querySelector(':scope > [class*="comments2--comment__content-"]');
                if (!own) return null;
                const author = own.querySelector('[data-testid="comment-author-link"]');
                const content = own.querySelector('[class*="comments2--comment-text__block-"]');
                return {
                    author: author?.innerText || '',
                    authorHref: author?.getAttribute('href') || '',
                    text: content?.innerText || '',
                    replies: Array.from(root.querySelectorAll('[data-testid="child-comment"]'))
                        .map((child) => {
                            const ownChild = child.querySelector(
                                ':scope > [class*="comments2--comment__content-"]'
                            );
                            return {
                                author: ownChild?.querySelector(
                                    '[data-testid="comment-author-link"]'
                                )?.innerText || '',
                                authorHref: ownChild?.querySelector(
                                    '[data-testid="comment-author-link"]'
                                )?.getAttribute('href') || '',
                                text: ownChild?.querySelector(
                                    '[class*="comments2--comment-text__block-"]'
                                )?.innerText || '',
                                addressee: ownChild?.querySelector(
                                    '[data-testid="comment-author-name"]'
                                )?.innerText || '',
                            };
                        }),
                };
            }"""
        )

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

    def _find_comment_node_with_scroll(
        self, comment_id: str, *, reply_id: int | None = None
    ):
        candidates_checked = 0
        reply_expansion_keys: set[Any] = set()
        reply_expansion_attempts: dict[Any, int] = {}
        reply_expansion_deadline: float | None = None
        try:
            node, pass_candidate_count = self._find_comment_node(comment_id)
        except Exception as exc:
            logger.info(
                "Dzen source comment lookup failed",
                extra={
                    "event": "publication_source_comment_search_failed",
                    "reply_id": reply_id,
                    "failure_stage": "studio_source_comment_search",
                    "failure_reason": "lookup_exception",
                    **_safe_lookup_exception_fields(exc),
                    "scroll_attempt_count": 0,
                    "candidates_checked": candidates_checked,
                },
            )
            raise
        candidates_checked = pass_candidate_count
        scroll_attempt_count = 0
        if node is not None:
            logger.info(
                "Dzen source comment search completed",
                extra={
                    "event": "publication_source_comment_search_completed",
                    "reply_id": reply_id,
                    "result": "found",
                    "scroll_attempt_count": scroll_attempt_count,
                    "candidates_checked": candidates_checked,
                },
            )
            return node

        phase = "scroll"
        try:
            for scroll_attempt_count in range(1, _REPLY_SEARCH_MAX_SCROLLS + 1):
                self._page.mouse.wheel(0, _REPLY_SEARCH_SCROLL_DELTA_Y)
                self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
                if (
                    reply_expansion_deadline is None
                    and self._page.query_selector_all(selectors.COMMENT_OPEN_MORE)
                ):
                    reply_expansion_deadline = (
                        monotonic() + _REPLY_EXPANSION_OPERATION_TIMEOUT_MS / 1_000
                    )
                self._expand_hidden_replies(
                    clicked_keys=reply_expansion_keys,
                    attempt_counts=reply_expansion_attempts,
                    deadline=reply_expansion_deadline,
                )
                phase = "lookup"
                node, pass_candidate_count = self._find_comment_node(comment_id)
                candidates_checked += pass_candidate_count
                if node is not None:
                    logger.info(
                        "Dzen source comment search completed",
                        extra={
                            "event": "publication_source_comment_search_completed",
                            "reply_id": reply_id,
                            "result": "found",
                            "scroll_attempt_count": scroll_attempt_count,
                            "candidates_checked": candidates_checked,
                        },
                    )
                    return node
                phase = "scroll"
            logger.info(
                "Dzen source comment search completed",
                extra={
                    "event": "publication_source_comment_search_completed",
                    "reply_id": reply_id,
                    "result": "not_found",
                    "failure_stage": "studio_source_comment_search",
                    "failure_reason": "source_comment_not_found",
                    "failure_type": "SourceCommentUnavailableError",
                    "failure_description": "source comment was not found after scrolling",
                    "scroll_attempt_count": scroll_attempt_count,
                    "candidates_checked": candidates_checked,
                },
            )
            return None
        except Exception as exc:
            logger.info(
                "Dzen source comment search failed",
                extra={
                    "event": "publication_source_comment_search_failed",
                    "reply_id": reply_id,
                    "failure_stage": "studio_source_comment_search",
                    "failure_reason": "scroll_exception" if phase == "scroll" else "lookup_exception",
                    **_safe_lookup_exception_fields(exc),
                    "scroll_attempt_count": scroll_attempt_count,
                    "candidates_checked": candidates_checked,
                },
            )
            raise
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
        candidates_checked = 0
        for node, post_href in self._iter_comment_nodes():
            candidates_checked += 1
            author_link = node.query_selector(selectors.COMMENT_AUTHOR_LINK)
            author_href = author_link.get_attribute("href") or "" if author_link else ""
            text_el = node.query_selector(selectors.COMMENT_TEXT)
            node_text = text_el.inner_text() if text_el else ""
            node_id = synthetic_id(post_href, author_href, node_text)
            if node_id == comment_id:
                return node, candidates_checked
        return None, candidates_checked

    def _submit_reply(
        self,
        node,
        text: str,
        *,
        auto_publish: bool,
        on_submit_attempt: Callable[[], None] | None = None,
        reply_id: int | None,
    ) -> None:
        correlation = {"reply_id": reply_id}
        reply_button = node.query_selector(selectors.COMMENT_REPLY_BUTTON)
        logger.info(
            "Dzen reply button search completed",
            extra={
                "event": "publication_reply_button_search",
                **correlation,
                "result": "found" if reply_button is not None else "missing",
                **(
                    {
                        "failure_stage": "publication_controls",
                        "failure_reason": "reply_button_not_found",
                        "failure_type": "ControlNotFound",
                        "failure_description": "reply button was not present",
                    }
                    if reply_button is None else {}
                ),
            },
        )
        try:
            reply_button.click()
        except Exception as exc:
            logger.info(
                "Dzen reply button click failed",
                extra={
                    "event": "publication_reply_button_failed",
                    **correlation,
                    "failure_stage": "publication_controls",
                    "failure_reason": "reply_button_click_failed",
                    **_safe_exception_fields(exc),
                },
            )
            raise
        reply_input = node.query_selector(selectors.REPLY_INPUT)
        if reply_input is None:
            logger.info(
                "Dzen reply input was not found",
                extra={
                    "event": "publication_reply_input_failed",
                    **correlation,
                    "failure_stage": "publication_controls",
                    "failure_reason": "reply_input_not_found",
                    "failure_type": "ControlNotFound",
                    "failure_description": "reply input was not present",
                },
            )
        try:
            reply_input.fill(text)
        except Exception as exc:
            logger.info(
                "Dzen reply input fill failed",
                extra={
                    "event": "publication_reply_input_failed",
                    **correlation,
                    "failure_stage": "publication_controls",
                    "failure_reason": "reply_input_fill_failed",
                    **_safe_exception_fields(exc),
                },
            )
            raise
        if auto_publish:
            send_button = node.query_selector(selectors.REPLY_SUBMIT)
            logger.info(
                "Dzen send button search completed",
                extra={
                    "event": "publication_send_button_search",
                    **correlation,
                    "attempt": 1,
                    "result": "found" if send_button is not None else "missing",
                    **(
                        {
                            "failure_stage": "publication_controls",
                            "failure_reason": "send_button_not_found",
                            "failure_type": "ControlNotFound",
                            "failure_description": "send button was not present",
                        }
                        if send_button is None else {}
                    ),
                },
            )
            if send_button is None:
                raise RuntimeError("Dzen reply send button was not found after filling")
            logger.info(
                "Attempting to click Dzen send button",
                extra={
                    "event": "publication_send_click_attempt",
                    **correlation,
                    "attempt": 1,
                },
            )
            if on_submit_attempt is not None:
                on_submit_attempt()
            try:
                send_button.click()
            except Exception as exc:
                logger.info(
                    "Dzen reply submit click failed",
                    extra={
                        "event": "publication_submit_failed",
                        **correlation,
                        "failure_stage": "publication_submit",
                        "failure_reason": "submit_exception",
                        "attempt": 1,
                        **_safe_exception_fields(exc),
                    },
                )
                raise
            self._wait_for_send_button_to_hide(node, wait_attempt=1, reply_id=reply_id)
        else:
            self._page.wait_for_timeout(5_000)

    def _wait_for_send_button_to_hide(
        self,
        node: Any,
        *,
        wait_attempt: int,
        reply_id: int | None,
    ) -> bool:
        for _ in range(
            _REPLY_SUBMIT_BUTTON_TIMEOUT_MS // _REPLY_SEARCH_WAIT_MS
        ):
            send_button = node.query_selector(selectors.REPLY_SUBMIT)
            if send_button is None or not send_button.is_visible():
                logger.info(
                    "Dzen send button visibility wait completed",
                    extra={
                        "event": "publication_send_button_wait",
                        "reply_id": reply_id,
                        "attempt": wait_attempt,
                        "result": "hidden",
                    },
                )
                return True
            self._page.wait_for_timeout(_REPLY_SEARCH_WAIT_MS)
        send_button = node.query_selector(selectors.REPLY_SUBMIT)
        hidden = send_button is None or not send_button.is_visible()
        logger.info(
            "Dzen send button visibility wait completed",
            extra={
                "event": "publication_send_button_wait",
                "reply_id": reply_id,
                "attempt": wait_attempt,
                "result": "hidden" if hidden else "still_visible",
                **(
                    {
                        "failure_stage": "publication_submit",
                        "failure_reason": "send_button_still_visible",
                        "failure_type": "SubmitAcknowledgmentTimeout",
                        "failure_description": "send button stayed visible after submit",
                    }
                    if not hidden else {}
                ),
            },
        )
        return hidden

    def _iter_comment_nodes(self):
        for group in self._page.query_selector_all(selectors.POST_GROUP):
            post_href = _post_href(group)
            for node in group.query_selector_all(selectors.COMMENT_NODE):
                yield node, post_href
