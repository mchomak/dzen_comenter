import inspect
import logging
from datetime import datetime, timedelta, timezone

import pytest

import dzen_commenter.dzen  # noqa: F401
from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.errors import (
    PublicationUnconfirmedError,
    SourceCommentUnavailableError,
)
from dzen_commenter.contracts.interfaces import DzenPage
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import DzenStudioPage, page as dzen_page, selectors
from dzen_commenter.dzen.page import is_video_post_url, synthetic_id
from dzen_commenter.db.repository import PostgresCommentRepository
from dzen_commenter.monitoring.logging_config import StructuredFormatter


class FakeText:
    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


class FakeButton:
    def __init__(
        self,
        on_click=None,
        *,
        hide_on_click: bool = False,
        text: str = "2 ответа",
    ) -> None:
        self.clicks = 0
        self.on_click = on_click
        self.hide_on_click = hide_on_click
        self._text = text
        self.visible = True
        self.attached = True
        self.click_timeouts: list[int | None] = []
        self.click_forces: list[bool] = []

    def click(self, *, timeout: int | None = None, force: bool = False) -> None:
        self.click_timeouts.append(timeout)
        self.click_forces.append(force)
        self.clicks += 1
        if self.hide_on_click:
            self.visible = False
        if self.on_click is not None:
            self.on_click()

    def is_visible(self) -> bool:
        return self.attached and self.visible

    def inner_text(self) -> str:
        return self._text

    def evaluate(self, script: str) -> int | str:
        if "editor--comment__block-" in script:
            return f"fake-comment:{id(self)}"
        return id(self)


class FakeInput:
    def __init__(self) -> None:
        self.filled: list[str] = []

    def fill(self, value: str) -> None:
        self.filled.append(value)


class FakeLink:
    def __init__(self, href: str) -> None:
        self._href = href

    def get_attribute(self, name: str):
        return self._href if name == "href" else None


class FakeCommentNode:
    def __init__(self, *, author_href: str, author: str, text: str, date: str | None):
        self.reply_button = FakeButton()
        self.reply_input = FakeInput()
        self.reply_submit = FakeButton(hide_on_click=True)
        self.query_selector_calls: list[str] = []
        self.published_replies: list[dict[str, str]] = []
        self.hidden_replies: list[dict[str, str]] = []
        self.visible_comment_children: list[FakeCommentNode] = []
        self.hidden_comment_children: list[FakeCommentNode] = []
        self.parent_node: FakeCommentNode | None = None
        self.scroll_calls = 0
        self.more_button = FakeButton(
            lambda: self.published_replies.extend(self.hidden_replies)
        )
        self.reply_more_button = FakeButton(self._expand_comment_children)
        self.has_thread_wrapper = True
        self._children = {
            selectors.COMMENT_AUTHOR_LINK: FakeLink(author_href),
            selectors.COMMENT_AUTHOR_TEXT: FakeText(author),
            selectors.COMMENT_TEXT: FakeText(text),
            selectors.COMMENT_REPLY_BUTTON: self.reply_button,
            selectors.REPLY_INPUT: self.reply_input,
            selectors.REPLY_SUBMIT: self.reply_submit,
        }
        if date is not None:
            self._children[selectors.COMMENT_DATE_TEXT] = FakeText(date)

    def query_selector(self, selector: str):
        self.query_selector_calls.append(selector)
        if selector == selectors.REPLY_SUBMIT and not self.reply_submit.attached:
            return None
        return self._children.get(selector)

    def query_selector_all(self, selector: str):
        if selector == selectors.COMMENT_OPEN_MORE:
            buttons = [self.reply_more_button] if self.hidden_comment_children else []
            for child in self.visible_comment_children:
                buttons.extend(child.query_selector_all(selector))
            return buttons
        if selector == selectors.COMMENT_NODE:
            return self.visible_comment_nodes()
        return []

    def _expand_comment_children(self) -> None:
        for child in self.hidden_comment_children:
            child.parent_node = self
        self.visible_comment_children.extend(self.hidden_comment_children)
        self.hidden_comment_children.clear()

    def visible_comment_nodes(self) -> list["FakeCommentNode"]:
        nodes = [self]
        for child in self.visible_comment_children:
            nodes.extend(child.visible_comment_nodes())
        return nodes

    def scroll_into_view_if_needed(self) -> None:
        self.scroll_calls += 1

    def evaluate(self, script: str, arg=None):
        if "authorHref:" in script:
            if self.parent_node is not None:
                author_link = self.parent_node.query_selector(selectors.COMMENT_AUTHOR_LINK)
                text_el = self.parent_node.query_selector(selectors.COMMENT_TEXT)
                return {
                    "authorHref": author_link.get_attribute("href") if author_link else "",
                    "text": text_el.inner_text() if text_el else "",
                }
            return None
        if isinstance(arg, dict):
            if not self.has_thread_wrapper:
                return None
            count = 1 + len(self.published_replies)
            if not self.hidden_replies:
                return {"count": count, "expanded": False}
            self.more_button.click()
            if len(self.published_replies) > count - 1:
                self.hidden_replies.clear()
            return {"count": count, "expanded": True}
        if arg != selectors.COMMENT_THREAD or not self.has_thread_wrapper:
            return None
        if "?.querySelectorAll" in script:
            return 1 + len(self.published_replies)
        return list(self.published_replies)


class FakeGroup:
    def __init__(
        self, post_href: str, nodes: list[FakeCommentNode], title: str | None = None
    ) -> None:
        self._post_link = FakeLink(post_href)
        self._nodes = nodes
        self._title = FakeText(title) if title is not None else None
        self.scroll_calls = 0

    def query_selector(self, selector: str):
        if selector == selectors.POST_LINK:
            if 'href^="/a/"' in selector and not self._post_link._href.startswith("/a/"):
                return None
            return self._post_link
        if selector == selectors.POST_TITLE:
            return self._title
        return None

    def query_selector_all(self, selector: str):
        if selector == selectors.COMMENT_NODE:
            return [
                node
                for root in self._nodes
                for node in root.visible_comment_nodes()
            ]
        if selector == selectors.COMMENT_OPEN_MORE:
            return [
                button
                for root in self._nodes
                for button in root.query_selector_all(selector)
            ]
        return []

    def scroll_into_view_if_needed(self) -> None:
        self.scroll_calls += 1


class FakeMouse:
    def __init__(self, page: "FakePage") -> None:
        self._page = page
        self.wheel_calls: list[tuple[float, float]] = []

    def wheel(self, delta_x: float, delta_y: float) -> None:
        self.wheel_calls.append((delta_x, delta_y))
        self._page.load_next_scroll_screen()


class FakePage:
    def __init__(
        self,
        groups: list[FakeGroup],
        *,
        scroll_groups: list[list[FakeGroup]] | None = None,
        cleanup_error: Exception | None = None,
    ) -> None:
        self._groups = groups
        self._scroll_groups = list(scroll_groups or [])
        self._cleanup_error = cleanup_error
        self.waited_ms: list[float] = []
        self.evaluate_calls: list[str] = []
        self.reload_calls: list[dict[str, str]] = []
        self.on_wait_timeout = None
        self.on_reload = None
        self.mouse = FakeMouse(self)
        self.context = FakeBrowserContext()
        self.listeners: dict[str, list] = {}

    def on(self, event: str, callback) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event: str, callback) -> None:
        self.listeners[event].remove(callback)

    def emit(self, event: str, value) -> None:
        for callback in list(self.listeners.get(event, [])):
            callback(value)

    def query_selector_all(self, selector: str):
        if selector == selectors.POST_GROUP:
            return list(self._groups)
        if selector == selectors.COMMENT_OPEN_MORE:
            return [
                button
                for group in self._groups
                for button in group.query_selector_all(selector)
            ]
        return []

    def wait_for_timeout(self, timeout_ms: float) -> None:
        self.waited_ms.append(timeout_ms)
        if self.on_wait_timeout is not None:
            self.on_wait_timeout(timeout_ms)

    def reload(self, **kwargs) -> None:
        self.reload_calls.append(kwargs)
        if self.on_reload is not None:
            self.on_reload()

    def load_next_scroll_screen(self) -> None:
        if self._scroll_groups:
            self._groups = self._scroll_groups.pop(0)

    def evaluate(self, script: str) -> None:
        self.evaluate_calls.append(script)
        if self._cleanup_error is not None:
            raise self._cleanup_error


class FakeBrowserContext:
    def __init__(self) -> None:
        self.article_pages: list["FakeArticlePage"] = []
        self.new_page_calls = 0

    def new_page(self) -> "FakeArticlePage":
        self.new_page_calls += 1
        return self.article_pages.pop(0)


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeArticlePage:
    def __init__(
        self,
        *,
        article_text: str = "",
        article_content: dict | None = None,
        goto_error: Exception | None = None,
        goto_errors: list[Exception | None] | None = None,
        goto_status: int | None = None,
        comments_available: bool = True,
        public_roots: list | None = None,
        hidden_roots: list | None = None,
        newest_roots: list | None = None,
        sort_label: str | None = None,
        newest_option_available: bool = True,
    ) -> None:
        self.article_text = article_text
        self.article_content = article_content
        self.goto_error = goto_error
        self.goto_errors = list(goto_errors or [])
        self.goto_status = goto_status
        self.comments_available = comments_available
        self.goto_calls: list[tuple[str, str]] = []
        self.goto_timeouts: list[int | None] = []
        self.close_calls = 0
        self.public_roots = list(public_roots or [])
        self.hidden_roots = list(hidden_roots or [])
        self.newest_roots = list(newest_roots or [])
        self.sort_label = sort_label
        self.newest_option_available = newest_option_available
        self.sort_open = False
        self.waited_ms: list[int] = []
        self.scroll_calls = 0
        self.more_button = FakeButton(self._load_more)
        self.sort_button = FakeButton(self._toggle_sort)
        self.sort_button.inner_text = lambda: self.sort_label or ""
        self.newest_button = FakeButton(self._sort_newest)

    def _load_more(self) -> None:
        self.public_roots.extend(self.hidden_roots)
        self.hidden_roots.clear()

    def _toggle_sort(self) -> None:
        self.sort_open = not self.sort_open

    def _sort_newest(self) -> None:
        self.public_roots = list(self.newest_roots)
        self.sort_label = "Сначала новые"
        self.sort_open = False

    def goto(self, url: str, *, wait_until: str, timeout: int | None = None) -> None:
        self.goto_calls.append((url, wait_until))
        self.goto_timeouts.append(timeout)
        if self.goto_errors:
            error = self.goto_errors.pop(0)
            if error is not None:
                raise error
        if self.goto_error is not None:
            raise self.goto_error
        return FakeResponse(self.goto_status) if self.goto_status is not None else None

    def query_selector(self, selector: str):
        if selector == "article" and self.article_text:
            return FakeText(self.article_text)
        if selector == selectors.ARTICLE_COMMENTS:
            return self if self.comments_available else None
        if selector == selectors.ARTICLE_MORE_COMMENTS and self.hidden_roots:
            return self.more_button
        if selector == selectors.ARTICLE_SORT and self.sort_label is not None:
            return self.sort_button
        if selector == selectors.ARTICLE_SORT_NEWEST and self.sort_open and self.newest_option_available:
            return self.newest_button
        return None

    def query_selector_all(self, selector: str):
        if selector == selectors.ARTICLE_ROOT_COMMENT:
            return list(self.public_roots)
        return []

    def scroll_into_view_if_needed(self) -> None:
        self.scroll_calls += 1

    def wait_for_timeout(self, timeout_ms: int) -> None:
        self.waited_ms.append(timeout_ms)

    def evaluate(self, _script: str):
        return self.article_content

    def close(self) -> None:
        self.close_calls += 1


class FakePublicRoot:
    def __init__(
        self,
        *,
        author: str,
        author_href: str,
        text: str,
        replies: list[dict[str, str]] | None = None,
        hidden_replies: list[dict[str, str]] | None = None,
    ) -> None:
        self.author = author
        self.author_href = author_href
        self.text = text
        self.replies = list(replies or [])
        self.hidden_replies = list(hidden_replies or [])
        self.expand_button = FakeButton(self._expand)

    def _expand(self) -> None:
        self.replies.extend(self.hidden_replies)
        self.hidden_replies.clear()

    def evaluate(self, _script: str) -> dict:
        return {
            "author": self.author,
            "authorHref": self.author_href,
            "text": self.text,
            "replies": list(self.replies),
        }

    def query_selector(self, selector: str):
        if selector == selectors.ARTICLE_OPEN_REPLIES and self.hidden_replies:
            return self.expand_button
        return None


def public_root_for(node: FakeCommentNode, *, reply_text: str = "мой ответ", reply_author: str = "Configured Bot") -> FakePublicRoot:
    return FakePublicRoot(
        author=node._children[selectors.COMMENT_AUTHOR_TEXT].inner_text(),
        author_href=node._children[selectors.COMMENT_AUTHOR_LINK].get_attribute("href"),
        text=node._children[selectors.COMMENT_TEXT].inner_text(),
        replies=[{"author": reply_author, "text": reply_text}],
    )


def make_node(i: int, date: str | None = None) -> FakeCommentNode:
    return FakeCommentNode(
        author_href=f"/user/u{i}",
        author=f"author{i}",
        text=f"text{i}",
        date=date,
    )


# Acceptance 2 — структурное соответствие контракту DzenPage.
def test_implements_dzen_page_contract():
    for name in ("fetch_comments", "fetch_article_text", "publish_reply"):
        proto_sig = inspect.signature(getattr(DzenPage, name))
        impl_sig = inspect.signature(getattr(DzenStudioPage, name))
        assert list(proto_sig.parameters) == list(impl_sig.parameters)


def test_fetch_article_text_uses_article_body_and_closes_temporary_tab(caplog):
    browser = FakePage([FakeGroup("/a/post", [])])
    article_page = FakeArticlePage(article_text="Article body")
    browser.context.article_pages = [article_page]
    page = DzenStudioPage(browser)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        assert page.fetch_article_text("https://dzen.ru/a/post") == "Article body"
    assert article_page.goto_calls == [("https://dzen.ru/a/post", "domcontentloaded")]
    assert article_page.close_calls == 1
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "article_text_fetch_completed"
    )
    assert record.article_text_length == len("Article body")
    assert "Article body" not in StructuredFormatter().format(record)


def test_fetch_article_text_does_not_fall_back_to_the_page_main_element(caplog):
    class MainOnlyArticlePage(FakeArticlePage):
        def query_selector(self, selector: str):
            if selector == "main":
                return FakeText("Page chrome and recommendations")
            return None

    browser = FakePage([FakeGroup("/a/post", [])])
    article_page = MainOnlyArticlePage()
    browser.context.article_pages = [article_page]
    page = DzenStudioPage(browser)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        assert page.fetch_article_text("https://dzen.ru/a/post") is None
    assert article_page.close_calls == 1
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "article_text_fetch_failed"
    )
    assert record.failure_stage == "article_text_extraction"
    assert record.failure_reason == "article_text_not_found"
    assert record.article_text_length == 0


def test_fetch_article_text_keeps_content_blocks_and_removes_promotional_noise():
    browser = FakePage([FakeGroup("/a/post", [])])
    article_page = FakeArticlePage(
        article_content={
            "title": "Article title",
            "blocks": [
                {"tag": "P", "text": "Useful article detail"},
                {"tag": "FIGCAPTION", "text": "Designed by Example"},
                {"tag": "P", "text": "Calculate with Domeo"},
                {"tag": "BLOCKQUOTE", "text": "Important conclusion"},
            ],
        }
    )
    browser.context.article_pages = [article_page]
    page = DzenStudioPage(browser)

    assert page.fetch_article_text("https://dzen.ru/a/post") == (
        "Article title\n\nUseful article detail\n\nImportant conclusion"
    )


def test_fetch_article_text_skips_video_posts_without_opening_a_tab():
    browser = FakePage([])
    page = DzenStudioPage(browser)

    assert page.fetch_article_text("https://dzen.ru/video/watch/vid1") is None
    assert browser.context.new_page_calls == 0


def test_fetch_article_text_closes_failed_temporary_tab_and_caches_none():
    browser = FakePage([FakeGroup("/a/post", [])])
    failed = FakeArticlePage(goto_error=RuntimeError("unavailable"))
    browser.context.article_pages = [failed]
    page = DzenStudioPage(browser)

    assert page.fetch_article_text("https://dzen.ru/a/post") is None
    assert page.fetch_article_text("https://dzen.ru/a/post") is None
    assert failed.close_calls == 1
    assert browser.context.new_page_calls == 1


def test_fetch_article_text_logs_safe_navigation_failure(caplog):
    browser = FakePage([FakeGroup("/a/post", [])])
    failed = FakeArticlePage(
        goto_error=RuntimeError(
            "navigation failed at https://dzen.ru/a/private?token=private-token"
            "#private-fragment private article text"
        )
    )
    browser.context.article_pages = [failed]
    page = DzenStudioPage(browser)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        assert page.fetch_article_text(
            "https://dzen.ru/a/private?token=private-token#private-fragment"
        ) is None

    records = [
        record for record in caplog.records
        if getattr(record, "event", None) == "article_text_fetch_failed"
    ]
    assert len(records) == 1
    assert records[0].failure_stage == "article_navigation"
    assert records[0].failure_reason == "navigation_exception"
    assert records[0].failure_type == "RuntimeError"
    assert records[0].failure_description == "browser navigation failed"
    serialized = StructuredFormatter().format(records[0])
    for secret in ("private-token", "private-fragment", "private article text", "https://"):
        assert secret not in serialized


@pytest.mark.parametrize(
    ("browser_cause", "expected_description"),
    [
        ("Page.goto: net::ERR_CONNECTION_RESET", "browser network error: net::ERR_CONNECTION_RESET"),
        ("Target page, context or browser has been closed", "browser target was closed"),
        ("Frame was detached", "browser frame was detached"),
        (
            "Execution context was destroyed, most likely because of a navigation",
            "browser execution context was destroyed",
        ),
        ("net::ERR_PRIVATE_TOKEN", "browser operation failed"),
    ],
)
def test_fetch_article_text_logs_allowlisted_browser_causes_without_secrets(
    caplog, browser_cause, expected_description
):
    browser = FakePage([FakeGroup("/a/post", [])])
    failed = FakeArticlePage(
        goto_error=RuntimeError(
            f"{browser_cause} at https://dzen.ru/a/private?token=private-token"
            "#private-fragment private-author private comment text"
            " selector [data-author=private-author] credential=private-secret"
        )
    )
    browser.context.article_pages = [failed]
    page = DzenStudioPage(browser)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        assert page.fetch_article_text(
            "https://dzen.ru/a/private?token=private-token#private-fragment"
        ) is None

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "article_text_fetch_failed"
    )
    assert record.failure_stage == "article_navigation"
    assert record.failure_reason == "navigation_exception"
    assert record.failure_description == expected_description
    serialized = StructuredFormatter().format(record)
    for secret in (
        "private-token",
        "private-fragment",
        "private-author",
        "private comment text",
        "data-author",
        "private-secret",
        "https://",
        "ERR_PRIVATE_TOKEN",
    ):
        assert secret not in serialized


def test_fetch_article_text_logs_http_error_without_changing_extraction(caplog):
    browser = FakePage([FakeGroup("/a/post", [])])
    article_page = FakeArticlePage(article_text="private article text", goto_status=503)
    browser.context.article_pages = [article_page]
    page = DzenStudioPage(browser)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        assert page.fetch_article_text("https://dzen.ru/a/post?token=private-token") == (
            "private article text"
        )

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "article_navigation_failed"
    )
    assert record.failure_stage == "article_navigation"
    assert record.failure_reason == "http_status"
    assert record.http_status == 503
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    assert "private-token" not in serialized
    assert "private article text" not in serialized


# Acceptance 3 — двухуровневый разбор: 2 группы (2 + 1 комментарий) → 3 Comment.
def test_fetch_comments_two_level_parse():
    groups = [
        FakeGroup("/a/post1", [make_node(0), make_node(1)]),
        FakeGroup("/a/post2", [make_node(2)]),
    ]
    page = DzenStudioPage(FakePage(groups))

    comments = page.fetch_comments()

    assert isinstance(comments, list)
    assert len(comments) == 3
    for i, c in enumerate(comments):
        assert isinstance(c, Comment)
        assert c.status == CommentStatus.NEW
        assert c.id is None
        assert c.publication_id == 0
        assert isinstance(c.fetched_at, datetime)
        assert c.fetched_at.tzinfo is None
        assert c.author == f"author{i}"
        assert c.text == f"text{i}"


@pytest.mark.parametrize(
    ("reply_count", "button_label"),
    [
        (1, "1 ответ"),
        (3, "3 ответа"),
        (5, "5 ответов"),
        (5, "5\u00a0ответов"),
    ],
)
def test_fetch_comments_expands_hidden_replies_without_reading_button_label(
    reply_count, button_label
):
    parent = make_node(0)
    parent.reply_more_button._text = button_label
    parent.hidden_comment_children = [make_node(index + 1) for index in range(reply_count)]
    page = DzenStudioPage(FakePage([FakeGroup("/a/post1", [parent])]))

    comments = page.fetch_comments()

    assert len(comments) == reply_count + 1
    assert parent.reply_more_button.clicks == 1
    assert all(
        comment.parent_comment_id == comments[0].dzen_comment_id
        for comment in comments[1:]
    )


def test_fetch_comments_expands_nested_reply_buttons_once_each():
    parent, child, grandchild = make_node(0), make_node(1), make_node(2)
    parent.hidden_comment_children = [child]
    child.hidden_comment_children = [grandchild]
    page = DzenStudioPage(FakePage([FakeGroup("/a/post1", [parent])]))

    comments = page.fetch_comments()

    assert [comment.text for comment in comments] == ["text0", "text1", "text2"]
    assert [
        comment.parent_comment_id for comment in comments
    ] == [None, comments[0].dzen_comment_id, comments[1].dzen_comment_id]
    assert parent.reply_more_button.clicks == 1
    assert child.reply_more_button.clicks == 1


def test_fetch_comments_fails_when_clicked_reply_control_stays_visible(caplog):
    parent = make_node(0)
    parent.hidden_comment_children = [make_node(1)]
    parent.reply_more_button.on_click = lambda: None
    page = DzenStudioPage(FakePage([FakeGroup("/a/post1", [parent])]))

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="reply expansion.*visible"):
            page.fetch_comments()

    incomplete = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "studio_reply_expansion_incomplete"
    )
    assert incomplete.failure_reason == "retry_limit_reached"
    assert parent.reply_more_button.clicks == dzen_page._REPLY_EXPANSION_MAX_ATTEMPTS
    assert not any(
        getattr(record, "event", None) == "studio_comments_read_completed"
        for record in caplog.records
    )


def test_studio_feed_scroll_avoids_waiting_for_an_unstable_comment_element():
    class UnstableComment(FakeCommentNode):
        def scroll_into_view_if_needed(self) -> None:
            raise TimeoutError("comment never became stable")

    comment = UnstableComment(
        author_href="/user/unstable",
        author="author",
        text="text",
        date=None,
    )
    group = FakeGroup("/a/post1", [comment])
    fake = FakePage([group])
    evaluate_calls = []
    fake.locator = lambda _selector: object()
    fake.evaluate = lambda script, arg: evaluate_calls.append((script, arg))

    DzenStudioPage(fake)._scroll_to_last_loaded_item([group])

    assert evaluate_calls == [
        (
            dzen_page._STUDIO_FEED_SCROLL_SCRIPT,
            {"group": selectors.POST_GROUP, "comment": selectors.COMMENT_NODE},
        )
    ]
    assert fake.mouse.wheel_calls == [(0, dzen_page._REPLY_SEARCH_SCROLL_DELTA_Y)]


def test_studio_feed_scroll_script_reaches_last_comment_in_scroll_container():
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    comments = "".join(
        '<div class="editor--comment__block-comment" style="height:100px">'
        f"comment {index}</div>"
        for index in range(6)
    )
    html = (
        '<div id="feed" style="height:100px; overflow:auto">'
        '<div data-testid="comment">'
        f"{comments}"
        "</div></div>"
    )

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)

            page.evaluate(
                dzen_page._STUDIO_FEED_SCROLL_SCRIPT,
                {"group": selectors.POST_GROUP, "comment": selectors.COMMENT_NODE},
            )

            assert page.locator("#feed").evaluate("element => element.scrollTop") > 0
        finally:
            browser.close()


def test_fetch_comments_logs_the_phase_of_a_feed_read_timeout(caplog):
    fake = FakePage([FakeGroup("/a/post1", [make_node(0)])])

    def fail_scroll(_delta_x, _delta_y):
        raise TimeoutError("feed scroll timed out")

    fake.mouse.wheel = fail_scroll
    page = DzenStudioPage(fake)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(TimeoutError, match="feed scroll timed out"):
            page.fetch_comments()

    failed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "studio_comments_read_failed"
    )
    assert failed.failure_phase == "scroll_to_last_loaded_item"


def test_sibling_reply_controls_keep_identity_and_noop_clicks_fail(
    caplog, monkeypatch
):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div data-testid="comment">
      <div class="editor--comments-page__postContainer-post">
        <a href="/a/post1"></a>
      </div>
      <div class="editor--comments-page__commentNode-thread">
        <div class="editor--comment__block-first">
          <a class="editor--comment__nameLink-author" href="/user/same"></a>
          <p class="editor--comment__text-text">duplicate text</p>
        </div>
        <button class="editor--root-comment__openMoreButton-first">
          Показать ответы
        </button>
        <div class="editor--comment__block-second">
          <a class="editor--comment__nameLink-author" href="/user/same"></a>
          <p class="editor--comment__text-text">duplicate text</p>
        </div>
        <button class="editor--root-comment__openMoreButton-second">
          Показать ответы
        </button>
      </div>
    </div>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            buttons = page.query_selector_all(selectors.COMMENT_OPEN_MORE)
            keys = [
                button.evaluate(dzen_page._REPLY_BUTTON_KEY_SCRIPT)
                for button in buttons
            ]

            assert len(keys) == 2
            assert all(isinstance(key, str) and key for key in keys)
            assert keys[0] != keys[1]

            page.set_content(html)
            rerendered_keys = [
                button.evaluate(dzen_page._REPLY_BUTTON_KEY_SCRIPT)
                for button in page.query_selector_all(selectors.COMMENT_OPEN_MORE)
            ]
            assert rerendered_keys == keys

            monkeypatch.setattr(dzen_page, "_REPLY_SEARCH_WAIT_MS", 0)
            with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
                with pytest.raises(RuntimeError, match="reply expansion.*visible"):
                    DzenStudioPage(page).fetch_comments()

            incomplete = next(
                record for record in caplog.records
                if getattr(record, "event", None)
                == "studio_reply_expansion_incomplete"
            )
            assert incomplete.failure_reason == "retry_limit_reached"
            assert incomplete.clicked_count == 4
            assert not any(
                getattr(record, "event", None) == "studio_comments_read_completed"
                for record in caplog.records
            )
        finally:
            browser.close()


def test_reply_control_click_is_dispatched_after_dom_validation():
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div data-testid="comment">
      <div class="editor--comments-page__postContainer-post">
        <a href="/a/post1"></a>
      </div>
      <div class="editor--comments-page__commentNode-thread">
        <div class="editor--comment__block-parent" style="min-height: 40px">
          <button class="editor--root-comment__openMoreButton-more"
            onclick="window.replyExpansionClicks++">Показать 1 ответ</button>
        </div>
      </div>
    </div>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            page.evaluate("window.replyExpansionClicks = 0")
            button = page.locator(selectors.COMMENT_OPEN_MORE)
            click_result = page.evaluate(
                dzen_page._REPLY_CONTROL_CLICK_SCRIPT,
                {
                    "group": selectors.POST_GROUP,
                    "more": selectors.COMMENT_OPEN_MORE,
                    "postLink": selectors.POST_LINK,
                    "postLinkFallback": selectors.POST_LINK_FALLBACK,
                    "groupIndex": 0,
                    "buttonIndex": 0,
                    "expectedPostHref": "/a/post1",
                    "expectedKey": button.evaluate(
                        dzen_page._REPLY_BUTTON_KEY_SCRIPT
                    ),
                    "expectedClass": button.get_attribute("class"),
                    "expectedText": button.inner_text(),
                },
            )

            assert click_result is True
            assert page.evaluate("window.replyExpansionClicks") == 0
            page.wait_for_timeout(50)
            assert page.evaluate("window.replyExpansionClicks") == 1
        finally:
            browser.close()


def test_hidden_reply_expansion_waits_for_delayed_reply_render():
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div data-testid="comment">
      <div class="editor--comments-page__postContainer-post">
        <a href="/a/post1"></a>
      </div>
      <div class="editor--comments-page__commentNode-thread">
        <div class="editor--comment__block-parent" style="min-height: 40px">
          <a class="editor--comment__nameLink-author" href="/user/parent"></a>
          <p class="editor--comment__text-text">parent comment</p>
          <button class="editor--root-comment__openMoreButton-more"
            onclick="window.replyExpansionClicks++; var button = this;
              setTimeout(function() {
                var reply = document.createElement('div');
                reply.className = 'editor--comment__block-reply';
                reply.style.minHeight = '40px';
                var author = document.createElement('a');
                author.className = 'editor--comment__nameLink-author';
                author.href = '/user/reply';
                var text = document.createElement('p');
                text.className = 'editor--comment__text-text';
                text.textContent = 'delayed reply';
                reply.append(author, text);
                button.parentElement.append(reply);
                button.remove();
              }, 900)">Показать 1 ответ</button>
        </div>
      </div>
    </div>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            page.evaluate("window.replyExpansionClicks = 0")

            clicked_count = DzenStudioPage(page)._expand_hidden_replies(
                expected_post_href="/a/post1",
                scope_index=0,
            )

            assert clicked_count == 1
            assert page.evaluate("window.replyExpansionClicks") == 1
            assert page.locator(selectors.COMMENT_NODE).count() == 2
            assert page.get_by_text("delayed reply").count() == 1
        finally:
            browser.close()


def test_hidden_reply_expansion_reads_controls_in_one_dom_snapshot(monkeypatch):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div data-testid="comment">
      <div class="editor--comments-page__postContainer-post">
        <a href="/a/post1"></a>
      </div>
      <div class="editor--comments-page__commentNode-first">
        <div class="editor--comment__block-first">
          <a class="editor--comment__nameLink-author" href="/user/1"></a>
          <p class="editor--comment__text-text">first comment</p>
        </div>
        <button class="editor--root-comment__openMoreButton-first"
          onclick="this.remove()">Показать 1 ответ</button>
      </div>
      <div class="editor--comments-page__commentNode-second">
        <div class="editor--comment__block-second">
          <a class="editor--comment__nameLink-author" href="/user/2"></a>
          <p class="editor--comment__text-text">second comment</p>
        </div>
        <button class="editor--root-comment__openMoreButton-second"
          onclick="this.remove()">Показать 3 ответа</button>
      </div>
    </div>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            monkeypatch.setattr(
                dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 0
            )

            expanded = DzenStudioPage(page)._expand_hidden_replies(
                scope=page.locator(selectors.POST_GROUP).nth(0),
                expected_post_href="/a/post1",
                scope_index=0,
            )

            assert expanded == 2
            assert page.query_selector_all(selectors.COMMENT_OPEN_MORE) == []
        finally:
            browser.close()


def test_hidden_reply_expansion_accepts_button_that_remains_after_replies_load(
    monkeypatch,
):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div data-testid="comment">
      <div class="editor--comments-page__postContainer-post">
        <a href="/a/post1"></a>
      </div>
      <div class="editor--comments-page__commentNode-first">
        <div class="editor--comment__block-first">
          <a class="editor--comment__nameLink-author" href="/user/1"></a>
          <p class="editor--comment__text-text">first comment</p>
        </div>
        <button id="show-replies"
          class="editor--root-comment__openMoreButton-first"
          onclick="this.parentElement.insertAdjacentHTML('beforeend', '<div class=editor--comment__block-reply>reply</div>')">
          Показать 1 ответ
        </button>
      </div>
    </div>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            monkeypatch.setattr(
                dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 0
            )

            expanded = DzenStudioPage(page)._expand_hidden_replies(
                scope=page.locator(selectors.POST_GROUP).nth(0),
                expected_post_href="/a/post1",
                scope_index=0,
            )

            assert expanded == 1
            assert page.locator(selectors.COMMENT_NODE).count() == 2
            assert page.locator("#show-replies").count() == 1
        finally:
            browser.close()


def test_reply_button_key_uses_group_scope_when_no_thread_wrapper_exists():
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div class="editor--comments-page__groupByPost-3D" data-testid="comment">
      <div class="editor--comments-page__commentsContainer-12">
        <div class="editor--comment__block-first">
          <a class="editor--comment__nameLink-author" href="/user/same"></a>
          <p class="editor--comment__text-text">duplicate text</p>
        </div>
        <button class="editor--root-comment__openMoreButton-first">
          Показать ответы
        </button>
        <div class="editor--comment__block-second">
          <a class="editor--comment__nameLink-author" href="/user/same"></a>
          <p class="editor--comment__text-text">duplicate text</p>
        </div>
        <button class="editor--root-comment__openMoreButton-second">
          Показать ответы
        </button>
      </div>
    </div>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            keys = [
                button.evaluate(dzen_page._REPLY_BUTTON_KEY_SCRIPT)
                for button in page.query_selector_all(selectors.COMMENT_OPEN_MORE)
            ]

            assert len(keys) == 2
            assert all(isinstance(key, str) and key for key in keys)
            assert keys[0] != keys[1]
        finally:
            browser.close()


def test_hidden_root_reply_control_is_deferred_until_its_thread_is_mounted(
    monkeypatch,
):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div class="editor--comments-page__groupByPost-3D" data-testid="comment">
      <div class="editor--comments-page__commentsContainer-12">
        <div class="editor--comments-page__commentNode-2f">
          <div class="editor--comment__block-parent">
            <a class="editor--comment__nameLink-author" href="/user/parent"></a>
            <p class="editor--comment__text-text">parent text</p>
          </div>
          <button
            class="editor--root-comment__openMoreButton-parent"
            onclick="window.replyClicks.push('parent'); document.getElementById('nested-comments').style.display = 'block'; this.remove()"
          >
            Показать ответы
          </button>
          <div
            id="nested-comments"
            class="editor--root-comment__commentsContainer-Ep"
            style="display: none"
          >
            <div class="editor--root-comment__commentNode-14">
              <div class="editor--comment__block-child">
                <a class="editor--comment__nameLink-author" href="/user/child"></a>
                <p class="editor--comment__text-text">child text</p>
              </div>
              <button
                class="editor--root-comment__openMoreButton-child"
                onclick="window.replyClicks.push('child'); this.remove()"
              >
                Показать ответы
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
    <script>window.replyClicks = [];</script>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            monkeypatch.setattr(
                dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 0
            )
            group = page.locator('[data-testid="comment"]')

            expanded = DzenStudioPage(page)._expand_hidden_replies(scope=group)

            assert expanded == 2
            assert page.evaluate("window.replyClicks") == ["parent", "child"]
        finally:
            browser.close()


def test_hidden_reply_expansion_reacquires_controls_after_group_rerender(
    monkeypatch,
):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div class="editor--comments-page__groupByPost-3D" data-testid="comment">
      <div class="editor--comments-page__postContainer-xf">
        <a href="/a/test-post"></a>
      </div>
      <div class="editor--comments-page__commentsContainer-12">
        <div class="editor--comments-page__commentNode-2f">
          <div class="editor--comment__block-parent">
            <a class="editor--comment__nameLink-author" href="/user/parent"></a>
            <p class="editor--comment__text-text">parent text</p>
          </div>
          <button
            class="editor--root-comment__openMoreButton-parent"
            onclick="window.replyClicks.push('parent'); window.replaceGroup()"
          >Показать 1 ответ</button>
        </div>
      </div>
    </div>
    <script>
      window.replyClicks = [];
      window.replaceGroup = () => {
        const group = document.querySelector('[data-testid="comment"]');
        const replacement = document.createElement('div');
        replacement.className = 'editor--comments-page__groupByPost-3D';
        replacement.setAttribute('data-testid', 'comment');
        replacement.innerHTML = `
          <div class="editor--comments-page__postContainer-xf">
            <a href="/a/test-post"></a>
          </div>
          <div class="editor--comments-page__commentsContainer-12">
            <div class="editor--comments-page__commentNode-2f">
              <div class="editor--comment__block-parent">
                <a class="editor--comment__nameLink-author" href="/user/parent"></a>
                <p class="editor--comment__text-text">parent text</p>
              </div>
              <div class="editor--root-comment__commentNode-14">
                <div class="editor--comment__block-child">
                  <a class="editor--comment__nameLink-author" href="/user/child"></a>
                  <p class="editor--comment__text-text">child text</p>
                </div>
                <button
                  class="editor--root-comment__openMoreButton-child"
                  onclick="window.replyClicks.push('child'); this.remove()"
                >Показать 2 ответа</button>
              </div>
            </div>
          </div>`;
        group.replaceWith(replacement);
      };
    </script>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            monkeypatch.setattr(
                dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 0
            )
            group = page.locator('[data-testid="comment"]')

            expanded = DzenStudioPage(page)._expand_hidden_replies(
                scope=group, expected_post_href="/a/test-post"
            )

            assert expanded == 2
            assert page.evaluate("window.replyClicks") == ["parent", "child"]
        finally:
            browser.close()


def test_studio_feed_snapshot_counts_comments_and_finds_only_reply_groups():
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div data-testid="comment">
      <div class="editor--comment__block-one"></div>
    </div>
    <div data-testid="comment">
      <div class="editor--comment__block-two"></div>
      <div class="editor--comment__block-three"></div>
      <button class="editor--root-comment__openMoreButton-two">Показать 2 ответа</button>
    </div>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            studio_page = DzenStudioPage(page)

            groups, counts = studio_page._studio_feed_snapshot()

            assert counts == (2, 3, 1)
            assert studio_page._groups_with_reply_controls(groups) == [1]
        finally:
            browser.close()


def test_hidden_reply_expansion_uses_group_identity_when_comment_owner_is_missing(
    monkeypatch,
):
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    html = """
    <div class="editor--comments-page__groupByPost-3D" data-testid="comment">
      <div class="editor--comments-page__postContainer-xf">
        <a href="/a/test-post"></a>
      </div>
      <button class="editor--root-comment__openMoreButton-first"
        onclick="window.replyClicks.push('first'); this.remove()">Показать 1 ответ</button>
      <button class="editor--root-comment__openMoreButton-second"
        onclick="window.replyClicks.push('second'); this.remove()">Показать 2 ответа</button>
      <button class="editor--root-comment__openMoreButton-third"
        onclick="window.replyClicks.push('third'); this.remove()">Показать 3 ответа</button>
      <button class="editor--root-comment__openMoreButton-fourth"
        onclick="window.replyClicks.push('fourth'); this.remove()">Показать 4 ответа</button>
    </div>
    <script>window.replyClicks = [];</script>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"local Chromium is unavailable: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            monkeypatch.setattr(
                dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 0
            )
            monkeypatch.setattr(dzen_page, "_REPLY_BUTTON_KEY_SCRIPT", "() => null")
            clicked_keys = set()

            expanded = DzenStudioPage(page)._expand_hidden_replies(
                scope=page.locator('[data-testid="comment"]'),
                clicked_keys=clicked_keys,
                expected_post_href="/a/test-post",
                scope_index=0,
            )

            assert expanded == 4
            assert page.evaluate("window.replyClicks") == [
                "first", "second", "third", "fourth"
            ]
            assert clicked_keys == set()
        finally:
            browser.close()


def test_hidden_reply_expansion_does_not_click_the_same_button_twice_in_one_pass():
    button = FakeButton(hide_on_click=True)
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button]
        if selector == selectors.COMMENT_OPEN_MORE and button.is_visible()
        else []
    )
    page = DzenStudioPage(fake)

    assert page._expand_hidden_replies() == 1
    assert button.clicks == 1


def test_hidden_reply_expansion_does_not_repeat_a_recreated_logical_button():
    clicks = 0
    queries = 0
    button_handles = []
    button_visible = True

    class RecreatedButton:
        def evaluate(self, script: str) -> int | str:
            if "editor--comment__block-" in script:
                return "post:/a/post1|comment:/user/1|same text|0"
            return id(self)

        def click(self, *, timeout: int | None = None, force: bool = False) -> None:
            nonlocal button_visible, clicks
            clicks += 1
            button_visible = False

    def query_buttons(selector: str):
        nonlocal button_visible, queries
        assert selector == selectors.COMMENT_OPEN_MORE
        queries += 1
        if queries > 5:
            raise AssertionError("reply expansion kept querying a recreated button")
        if not button_visible:
            return []
        button = RecreatedButton()
        button_handles.append(button)
        return [button]

    fake = FakePage([])
    fake.query_selector_all = query_buttons
    page = DzenStudioPage(fake)
    clicked_keys = set()

    assert page._expand_hidden_replies(clicked_keys=clicked_keys) == 1
    button_visible = True
    assert page._expand_hidden_replies(clicked_keys=clicked_keys) == 0
    assert clicks == 1


def test_hidden_reply_expansion_prioritizes_new_controls_before_retries():
    actions = []
    child_visible = False

    class Control:
        def __init__(self, key: str) -> None:
            self.key = key

        def evaluate(self, script: str) -> int | str:
            if "editor--comment__block-" in script:
                return self.key
            return id(self)

        def click(self, *, timeout: int | None = None, force: bool = False) -> None:
            nonlocal child_visible
            actions.append(self.key)
            child_visible = self.key == "parent"

    parent = Control("parent")
    child = Control("child")
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [parent, *([child] if child_visible else [])]
        if selector == selectors.COMMENT_OPEN_MORE
        else []
    )
    page = DzenStudioPage(fake)

    with pytest.raises(RuntimeError, match="reply expansion.*visible"):
        page._expand_hidden_replies()

    assert actions == ["parent", "child", "parent", "parent"]


def test_hidden_reply_expansion_caps_playwright_click_timeout():
    button = FakeButton()

    def timeout_click(*, timeout=None, force=False):
        button.click_timeouts.append(timeout)
        button.click_forces.append(force)
        raise TimeoutError("reply click timed out")

    button.click = timeout_click
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button] if selector == selectors.COMMENT_OPEN_MORE else []
    )
    page = DzenStudioPage(fake)

    with pytest.raises(TimeoutError, match="reply click timed out"):
        page._expand_hidden_replies()

    assert button.clicks == 0
    assert button.click_forces == [True] * dzen_page._REPLY_EXPANSION_MAX_ATTEMPTS
    assert button.click_timeouts == [
        dzen_page._REPLY_EXPANSION_CLICK_TIMEOUT_MS
    ] * dzen_page._REPLY_EXPANSION_MAX_ATTEMPTS


def test_hidden_reply_expansion_retries_transient_not_visible_click_with_fresh_control(
    monkeypatch,
):
    successful_clicks = 0
    attempts = 0
    controls = []

    class ReplacedControl:
        def evaluate(self, script: str) -> int | str:
            if "editor--comment__block-" in script:
                return "stable-control-identity"
            return id(self)

        def is_visible(self) -> bool:
            return successful_clicks == 0

        def click(self, *, timeout: int | None = None, force: bool = False) -> None:
            nonlocal attempts, successful_clicks
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Element is not visible")
            successful_clicks += 1

    def query_controls(selector: str):
        assert selector == selectors.COMMENT_OPEN_MORE
        if successful_clicks:
            return []
        control = ReplacedControl()
        controls.append(control)
        return [control]

    fake = FakePage([])
    fake.query_selector_all = query_controls
    monkeypatch.setattr(dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 0)

    expanded = DzenStudioPage(fake)._expand_hidden_replies()

    assert expanded == 1
    assert attempts == 2
    assert len(controls) >= 2


def test_hidden_reply_expansion_retries_transient_click_timeout(
    monkeypatch,
):
    button = FakeButton(hide_on_click=True)
    attempts = 0

    def timeout_once(*, timeout=None, force=False):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("Timeout 5000ms exceeded")
        button.visible = False

    button.click = timeout_once
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button]
        if selector == selectors.COMMENT_OPEN_MORE and button.is_visible()
        else []
    )
    monkeypatch.setattr(dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 0)

    expanded = DzenStudioPage(fake)._expand_hidden_replies()

    assert expanded == 1
    assert attempts == 2


def test_hidden_reply_expansion_reconciles_timeout_after_control_disappears():
    button = FakeButton()
    attempts = 0

    def click_then_timeout(*, timeout=None, force=False):
        nonlocal attempts
        attempts += 1
        button.visible = False
        raise TimeoutError("Timeout after click dispatch")

    button.click = click_then_timeout
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button]
        if selector == selectors.COMMENT_OPEN_MORE and button.is_visible()
        else []
    )

    expanded = DzenStudioPage(fake)._expand_hidden_replies()

    assert expanded == 1
    assert attempts == 1


def test_hidden_reply_expansion_retries_transient_control_snapshot_timeout():
    button = FakeButton(hide_on_click=True)
    original_evaluate = button.evaluate
    evaluation_attempts = 0

    def evaluate_with_transient_timeout(script):
        nonlocal evaluation_attempts
        evaluation_attempts += 1
        if evaluation_attempts == 1:
            raise TimeoutError("control snapshot timed out during rerender")
        return original_evaluate(script)

    button.evaluate = evaluate_with_transient_timeout
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button] if selector == selectors.COMMENT_OPEN_MORE and button.is_visible() else []
    )

    expanded = DzenStudioPage(fake)._expand_hidden_replies(
        initial_controls=[button]
    )

    assert expanded == 1
    assert evaluation_attempts == 2
    assert button.clicks == 1


def test_hidden_reply_expansion_reports_click_safety_limit(monkeypatch):
    monkeypatch.setattr(dzen_page, "_REPLY_EXPANSION_MAX_CLICKS", 2, raising=False)
    clicks = 0
    queries = 0
    button_handles = []

    class DistinctButton:
        def evaluate(self, script: str) -> int | str:
            if "editor--comment__block-" in script:
                return f"distinct:{id(self)}"
            return id(self)

        def click(self, *, timeout: int | None = None, force: bool = False) -> None:
            nonlocal clicks
            clicks += 1

    def query_buttons(selector: str):
        nonlocal queries
        assert selector == selectors.COMMENT_OPEN_MORE
        queries += 1
        button = DistinctButton()
        button_handles.append(button)
        return [button]

    fake = FakePage([])
    fake.query_selector_all = query_buttons
    page = DzenStudioPage(fake)

    with pytest.raises(RuntimeError, match="reply expansion .*limit"):
        page._expand_hidden_replies()

    assert clicks == 2
    assert queries <= 3


def test_hidden_reply_expansion_reports_operation_deadline(caplog):
    button = FakeButton()
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button] if selector == selectors.COMMENT_OPEN_MORE else []
    )
    page = DzenStudioPage(fake)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="reply expansion reached its time limit"):
            page._expand_hidden_replies(deadline=0)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "studio_reply_expansion_incomplete"
    )
    assert record.failure_reason == "time_limit_reached"
    assert record.clicked_count == 0
    assert button.clicks == 0


def test_hidden_reply_expansion_handles_many_distinct_buttons():
    buttons = [FakeButton(hide_on_click=True) for _ in range(80)]
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button for button in buttons if button.visible]
        if selector == selectors.COMMENT_OPEN_MORE
        else []
    )
    page = DzenStudioPage(fake)

    assert page._expand_hidden_replies() == len(buttons)
    assert sum(button.clicks for button in buttons) == len(buttons)


def test_hidden_reply_expansion_allows_a_large_feed_more_than_three_minutes(
    monkeypatch,
):
    buttons = [FakeButton(hide_on_click=True) for _ in range(250)]
    fake = FakePage([])
    elapsed_ms = 0

    def wait_for_timeout(timeout_ms: float) -> None:
        nonlocal elapsed_ms
        fake.waited_ms.append(timeout_ms)
        elapsed_ms += timeout_ms

    fake.query_selector_all = lambda selector: (
        [button for button in buttons if button.is_visible()]
        if selector == selectors.COMMENT_OPEN_MORE
        else []
    )
    fake.wait_for_timeout = wait_for_timeout
    monkeypatch.setattr(dzen_page, "_REPLY_EXPANSION_MAX_CLICKS", 500)
    monkeypatch.setattr(dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 750)
    monkeypatch.setattr(dzen_page, "monotonic", lambda: elapsed_ms / 1_000)

    page = DzenStudioPage(fake)

    assert page._expand_hidden_replies() == len(buttons)
    assert sum(button.clicks for button in buttons) == len(buttons)
    assert elapsed_ms > 180_000


def test_hidden_reply_expansion_supports_more_than_200_controls_in_one_feed():
    buttons = [FakeButton(hide_on_click=True) for _ in range(418)]
    fake = FakePage([])
    fake.query_selector_all = lambda selector: (
        [button for button in buttons if button.is_visible()]
        if selector == selectors.COMMENT_OPEN_MORE
        else []
    )

    page = DzenStudioPage(fake)

    assert page._expand_hidden_replies() == len(buttons)
    assert sum(button.clicks for button in buttons) == len(buttons)


def test_fetch_comments_expands_replies_across_a_large_feed_after_three_minutes(
    monkeypatch,
):
    groups = []
    for index in range(250):
        parent = make_node(index)
        parent.hidden_comment_children = [make_node(index + 250)]
        groups.append(FakeGroup(f"/a/post{index}", [parent]))

    fake = FakePage(groups)
    elapsed_ms = 0

    def wait_for_timeout(timeout_ms: float) -> None:
        nonlocal elapsed_ms
        fake.waited_ms.append(timeout_ms)
        elapsed_ms += timeout_ms

    fake.wait_for_timeout = wait_for_timeout
    monkeypatch.setattr(dzen_page, "_REPLY_EXPANSION_MAX_CLICKS", 500)
    monkeypatch.setattr(dzen_page, "_REPLY_EXPANSION_POST_CLICK_WAIT_MS", 750)
    monkeypatch.setattr(dzen_page, "monotonic", lambda: elapsed_ms / 1_000)

    comments = DzenStudioPage(fake).fetch_comments()

    assert len(comments) == 500
    assert elapsed_ms > 180_000


def test_fetch_comments_expands_replies_for_826_publications_within_time_limit(
    monkeypatch,
):
    groups = []
    for index in range(826):
        parent = make_node(index)
        parent.hidden_comment_children = [make_node(index + 826)]
        groups.append(FakeGroup(f"/a/post{index}", [parent]))

    scroll_groups = [
        groups[: min(1 + (index + 1) * 23, len(groups))]
        for index in range(36)
    ]
    scroll_groups.extend([groups, groups, groups])
    fake = FakePage(groups[:1], scroll_groups=scroll_groups)
    elapsed_ms = 0

    def wait_for_timeout(timeout_ms: float) -> None:
        nonlocal elapsed_ms
        fake.waited_ms.append(timeout_ms)
        elapsed_ms += timeout_ms

    fake.wait_for_timeout = wait_for_timeout
    monkeypatch.setattr(dzen_page, "monotonic", lambda: elapsed_ms / 1_000)

    comments = DzenStudioPage(fake).fetch_comments()

    assert len(comments) == 1_652
    assert 39 <= len(fake.mouse.wheel_calls) <= dzen_page._STUDIO_FEED_MAX_SCAN_PASSES
    assert elapsed_ms < dzen_page._REPLY_EXPANSION_OPERATION_TIMEOUT_MS


def test_fetch_comments_waits_for_lazy_groups_until_three_stable_passes():
    first = FakeGroup("/a/post1", [make_node(0)])
    second = FakeGroup("/a/post2", [make_node(1)])
    fake = FakePage([first], scroll_groups=[[first, second]])
    page = DzenStudioPage(fake)

    comments = page.fetch_comments()

    assert [comment.text for comment in comments] == ["text0", "text1"]
    assert len(fake.mouse.wheel_calls) == 4
    assert fake.waited_ms == [dzen_page._REPLY_SEARCH_WAIT_MS] * 4


def test_fetch_comments_logs_incomplete_result_at_scan_pass_limit(caplog):
    groups = [FakeGroup(f"/a/post{index}", [make_node(index)]) for index in range(41)]
    scroll_groups = [groups[: index + 2] for index in range(40)]
    fake = FakePage([groups[0]], scroll_groups=scroll_groups)
    page = DzenStudioPage(fake)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(
            dzen_page.StudioFeedScanIncompleteError,
            match="Studio feed scan reached its pass limit",
        ) as error:
            page.fetch_comments()

    assert "after 40 passes" in str(error.value)
    assert len(fake.mouse.wheel_calls) == dzen_page._STUDIO_FEED_MAX_SCAN_PASSES
    incomplete = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "studio_comments_read_incomplete"
    )
    assert incomplete.failure_reason == "stability_pass_limit_reached"
    assert incomplete.scan_pass_count == dzen_page._STUDIO_FEED_MAX_SCAN_PASSES
    assert incomplete.comments_extracted == 41
    assert not any(
        getattr(record, "event", None) == "studio_comments_read_completed"
        for record in caplog.records
    )


# Acceptance 4 — синтетический id детерминирован и различает комментарии.
def test_synthetic_id_deterministic_and_distinct():
    groups = [
        FakeGroup("/a/post1", [make_node(0), make_node(1)]),
        FakeGroup("/a/post2", [make_node(2)]),
    ]
    page = DzenStudioPage(FakePage(groups))

    first = page.fetch_comments()
    second = page.fetch_comments()

    ids = [c.dzen_comment_id for c in first]
    assert len(set(ids)) == 3  # три разных комментария — три разных id
    assert [c.dzen_comment_id for c in second] == ids  # детерминированность


# Acceptance 5 — posted_at: минуты разбираются, прочее/пусто → None.
def test_posted_at_relative_minutes():
    now = datetime.now(timezone.utc)
    groups = [
        FakeGroup(
            "/a/post1",
            [
                make_node(0, date="8 мин"),
                make_node(1, date="3 дня"),
                make_node(2, date=None),
            ],
        )
    ]
    page = DzenStudioPage(FakePage(groups))

    comments = page.fetch_comments()

    minutes_ago = comments[0].posted_at
    assert minutes_ago is not None
    delta = comments[0].fetched_at - minutes_ago
    assert abs(delta - timedelta(minutes=8)) < timedelta(seconds=5)
    assert comments[1].posted_at is None  # "3 дня" не распознан
    assert comments[2].posted_at is None  # даты нет вовсе


# Acceptance 6 — parent_comment_id всегда None.
def test_parent_comment_id_always_none():
    groups = [
        FakeGroup("/a/post1", [make_node(0), make_node(1)]),
        FakeGroup("/a/post2", [make_node(2, date="8 мин")]),
    ]
    page = DzenStudioPage(FakePage(groups))
    for c in page.fetch_comments():
        assert c.parent_comment_id is None


# Publication confirmation uses the source thread in Studio and the public article.
def make_publication_page(node, *, article=None, bot_name="Configured Bot"):
    fake = FakePage([FakeGroup("/a/post1", [node])])
    article = article or FakeArticlePage(public_roots=[public_root_for(node)])
    preflight = FakeArticlePage(public_roots=[FakePublicRoot(
        author=node._children[selectors.COMMENT_AUTHOR_TEXT].inner_text(),
        author_href=node._children[selectors.COMMENT_AUTHOR_LINK].get_attribute("href"),
        text=node._children[selectors.COMMENT_TEXT].inner_text(),
    )])
    fake.context.article_pages = [preflight, article]
    return fake, DzenStudioPage(fake, bot_account_name_provider=lambda: bot_name), article


def test_publish_reply_targets_matching_node_without_reloading_studio():
    first, target_node = make_node(0), make_node(1)
    fake = FakePage([FakeGroup("/a/post1", [first, target_node])])
    article = FakeArticlePage(public_roots=[public_root_for(target_node)])
    fake.context.article_pages = [
        FakeArticlePage(public_roots=[FakePublicRoot(
            author="author1", author_href="/user/u1", text="text1"
        )]),
        article,
    ]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    comment = page.fetch_comments()[1]
    target_node.reply_submit.on_click = lambda: target_node.published_replies.append(
        {"author": "Configured Bot", "text": "мой ответ"}
    )

    page.publish_reply(comment, "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert target_node.reply_button.clicks == target_node.reply_submit.clicks == 1
    assert target_node.reply_input.filled == ["мой ответ"]
    assert first.reply_submit.clicks == 0
    assert fake.reload_calls == []
    assert dzen_page._STUDIO_CONFIRM_DELAY_MS in fake.waited_ms
    assert article.goto_calls == [("https://dzen.ru/a/post1", "commit")]
    assert article.goto_timeouts == [dzen_page._PUBLIC_NAVIGATION_TIMEOUT_MS]
    assert article.scroll_calls == 1
    assert article.close_calls == 1
    assert fake.listeners == {"request": [], "response": []}


def test_domeo_author_identity_confirms_both_pages():
    bot_name = "DOMEO | РЕМОНТ КВАРТИР | НЕДВИЖИМОСТЬ"
    node = make_node(0)
    article = FakeArticlePage(
        public_roots=[public_root_for(node, reply_author=bot_name)]
    )
    fake, page, _ = make_publication_page(node, article=article, bot_name=bot_name)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": bot_name, "text": "мой ответ"}
    )

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert node.reply_submit.clicks == 1
    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_auto_publication_logs_both_confirmations_without_reply_text(caplog):
    node = make_node(0)
    fake, page, _ = make_publication_page(
        node,
        article=FakeArticlePage(
            public_roots=[public_root_for(node, reply_text="private-reply-content")]
        ),
    )
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "private-reply-content"}
    )
    comment = page.fetch_comments()[0]
    log = logging.getLogger("dzen_commenter.dzen.page")

    with caplog.at_level(logging.INFO, logger=log.name):
        page.publish_reply(comment, "private-reply-content", auto_publish=True, reply_id=73, before_submit=lambda: None)

    records = [record for record in caplog.records if record.name == log.name]
    events = [record.event for record in records]
    assert events.index("publication_studio_visibility_check") < events.index("publication_article_check_started")
    assert events.index("publication_article_reply_found") < events.index("publication_confirmed")
    assert all(not hasattr(record, "comment_id") for record in records)
    assert all(record.reply_id == 73 for record in records if hasattr(record, "reply_id"))
    search = next(
        record for record in records
        if record.event == "publication_source_comment_search_completed"
    )
    assert search.result == "found"
    assert search.reply_id == 73
    serialized = "\n".join(StructuredFormatter().format(record) for record in records)
    for secret in ("private-reply-content", "text0", "author0", "/a/post1"):
        assert secret not in serialized


def test_missing_send_button_fails_before_public_check(caplog):
    node = make_node(0)
    node._children.pop(selectors.REPLY_SUBMIT)
    fake, page, article = make_publication_page(node)
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="send button was not found"):
            page.publish_reply(comment, "private-reply-content", auto_publish=True, before_submit=lambda: None)

    assert fake.reload_calls == []
    assert article.goto_calls == []
    assert node.reply_submit.clicks == 0
    failure = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_send_button_search"
    )
    assert failure.result == "missing"
    assert failure.failure_stage == "publication_controls"
    assert failure.failure_reason == "send_button_not_found"
    assert failure.failure_type == "ControlNotFound"
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    assert "private-reply-content" not in serialized
    assert not any(hasattr(record, "comment_id") for record in caplog.records)


def test_submit_exception_logs_safe_failure_details(caplog):
    node = make_node(0)
    fake, page, _ = make_publication_page(node)

    def fail_submit():
        raise RuntimeError(
            "submit failed for private reply at "
            "https://dzen.ru/a/private?token=private-token#fragment"
        )

    node.reply_submit.on_click = fail_submit
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(PublicationUnconfirmedError, match="publication not confirmed"):
            page.publish_reply(comment, "private reply text", auto_publish=True, reply_id=73, before_submit=lambda: None)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_submit_failed"
    )
    assert record.failure_stage == "publication_submit"
    assert record.failure_reason == "submit_exception"
    assert record.failure_type == "RuntimeError"
    assert record.reply_id == 73
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    for secret in ("private reply text", "private-token", "fragment", "https://"):
        assert secret not in serialized
    assert fake.listeners == {"request": [], "response": []}


def test_send_button_clicks_once_even_when_visibility_is_delayed():
    node = make_node(0)
    node.reply_submit.hide_on_click = False
    fake, page, article = make_publication_page(node)

    def submit():
        waits = 0

        def reveal(_timeout_ms):
            nonlocal waits
            waits += 1
            if waits == 3:
                node.reply_submit.visible = False
                node.published_replies.append(
                    {"author": "Configured Bot", "text": "мой ответ"}
                )

        fake.on_wait_timeout = reveal

    node.reply_submit.on_click = submit
    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.reply_submit.clicks == 1
    assert len(fake.waited_ms) >= 3
    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_send_button_still_visible_does_not_trigger_second_click():
    node = make_node(0)
    node.reply_submit.hide_on_click = False
    fake, page, article = make_publication_page(node)
    with pytest.raises(PublicationUnconfirmedError, match="publication not confirmed"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.reply_submit.clicks == 1
    assert article.goto_calls == []
    assert fake.listeners == {"request": [], "response": []}


def test_submit_marker_is_saved_before_click():
    node = make_node(0)
    _, page, _ = make_publication_page(node)
    events = []

    def submit():
        events.append("click")
        node.published_replies.append(
            {"author": "Configured Bot", "text": "мой ответ"}
        )

    node.reply_submit.on_click = submit
    page.publish_reply(
        page.fetch_comments()[0],
        "мой ответ",
        auto_publish=True,
        before_submit=lambda: events.append("marker"),
    )

    assert events == ["marker", "click"]
    assert node.reply_submit.clicks == 1


def test_submit_marker_failure_prevents_click():
    node = make_node(0)
    _, page, _ = make_publication_page(node)

    def marker_failure():
        raise RuntimeError("marker unavailable")

    with pytest.raises(RuntimeError, match="marker unavailable"):
        page.publish_reply(
            page.fetch_comments()[0],
            "мой ответ",
            auto_publish=True,
            before_submit=marker_failure,
        )

    assert node.reply_submit.clicks == 0


def test_missing_submit_marker_prevents_click():
    node = make_node(0)
    _, page, _ = make_publication_page(node)

    with pytest.raises(RuntimeError, match="durable publication submit marker is required"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True)

    assert node.reply_submit.clicks == 0


def test_public_verification_deadline_prevents_submit(monkeypatch):
    node = make_node(0)
    _, page, _ = make_publication_page(node)
    ticks = iter((0, dzen_page._PUBLIC_VERIFICATION_TIMEOUT_MS / 1_000))
    monkeypatch.setattr(dzen_page, "monotonic", lambda: next(ticks))

    with pytest.raises(RuntimeError, match="verification time limit reached"):
        page.publish_reply(
            page.fetch_comments()[0], "мой ответ", auto_publish=True,
            before_submit=lambda: None,
        )

    assert node.reply_submit.clicks == 0


def test_publication_lease_exceeds_explicit_wait_budget():
    polls = dzen_page._REPLY_SUBMIT_ACK_TIMEOUT_MS // dzen_page._REPLY_SEARCH_WAIT_MS
    thread_checks = 3 + polls * 2
    wait_budget_ms = (
        2 * dzen_page._PUBLIC_VERIFICATION_TIMEOUT_MS
        + dzen_page._REPLY_SEARCH_MAX_SCROLLS * dzen_page._REPLY_SEARCH_WAIT_MS
        + thread_checks * dzen_page._REPLY_EXPANSION_TIMEOUT_MS
        + 2 * dzen_page._REPLY_SUBMIT_ACK_TIMEOUT_MS
        + dzen_page._REPLY_SUBMIT_BUTTON_TIMEOUT_MS
    )
    lease_ms = PostgresCommentRepository._PUBLICATION_CLAIM_LEASE.total_seconds() * 1_000

    assert lease_ms > wait_budget_ms


def test_accepted_create_response_without_studio_reply_does_not_confirm():
    node = make_node(0)
    fake, page, article = make_publication_page(node)
    reply_text = "private reply"

    class Request:
        method = "POST"
        url = "https://dzen.ru/api/comments/create?token=private-token"
        post_data_json = {"text": reply_text, "replyToId": "source-id"}

    class Response:
        request = Request()
        status = 200

        def json(self):
            return {
                "status": "ok",
                "comments": [{
                    "id": "created-id", "text": reply_text,
                    "replyToId": "source-id", "visibility": "visible",
                }],
            }

    def submit():
        fake.emit("request", Response.request)
        fake.emit("response", Response())

    node.reply_submit.on_click = submit
    with pytest.raises(PublicationUnconfirmedError, match="publication not confirmed") as exc_info:
        page.publish_reply(page.fetch_comments()[0], reply_text, auto_publish=True, before_submit=lambda: None)

    assert "creation_outcome=accepted" in str(exc_info.value)
    assert "private-token" not in str(exc_info.value)
    assert reply_text not in str(exc_info.value)
    assert node.reply_submit.clicks == 1
    assert article.goto_calls == []
    assert fake.listeners == {"request": [], "response": []}


@pytest.mark.parametrize(
    "status,created,expected_outcome",
    [
        (403, {"id": "id", "text": "мой ответ", "visibility": "visible"}, "non_2xx"),
        (200, None, "invalid_response"),
        (200, {"id": "", "text": "мой ответ", "visibility": "visible"}, "missing_created_id"),
        (200, {"id": "id", "text": "other", "visibility": "visible"}, "text_mismatch"),
        (200, {"id": "id", "text": "мой ответ", "visibility": "hidden"}, "visibility_mismatch"),
        (200, {"id": "id", "text": "мой ответ", "visibility": "visible", "replyToId": "wrong"}, "relation_mismatch"),
    ],
)
def test_create_response_outcome_is_diagnostic_not_publication_proof(
    status, created, expected_outcome
):
    node = make_node(0)
    fake, page, article = make_publication_page(node)

    class Request:
        method = "POST"
        url = "https://dzen.ru/api/comments/create?token=private-token"
        post_data_json = {"text": "мой ответ", "replyToId": "source-id"}

    class Response:
        request = Request()

        def __init__(self):
            self.status = status

        def json(self):
            return {"status": "ok", "comments": [created] if created else []}

    def submit():
        fake.emit("request", Response.request)
        fake.emit("response", Response())

    node.reply_submit.on_click = submit
    expected_error = RuntimeError if expected_outcome == "non_2xx" else PublicationUnconfirmedError
    with pytest.raises(expected_error) as exc_info:
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    if expected_outcome == "non_2xx":
        assert type(exc_info.value) is RuntimeError
        assert "non-2xx" in str(exc_info.value)
    else:
        assert f"creation_outcome={expected_outcome}" in str(exc_info.value)
    assert "private-token" not in str(exc_info.value)
    assert article.goto_calls == []
    assert node.reply_submit.clicks == 1


def test_draft_does_not_submit_or_open_article():
    node = make_node(0)
    fake, page, article = make_publication_page(node)
    page.publish_reply(page.fetch_comments()[0], "draft", auto_publish=False)
    assert node.reply_button.clicks == 1
    assert node.reply_input.filled == ["draft"]
    assert node.reply_submit.clicks == 0
    assert article.goto_calls == []
    assert fake.reload_calls == []


def test_click_alone_does_not_confirm_publication():
    node = make_node(0)
    fake, page, article = make_publication_page(node)
    with pytest.raises(RuntimeError, match="not confirmed"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.reply_submit.clicks == 1
    assert article.goto_calls == []
    assert fake.reload_calls == []


def test_studio_reply_from_wrong_author_does_not_confirm():
    node = make_node(0)
    fake, page, article = make_publication_page(node)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Another Author", "text": "мой ответ"}
    )
    with pytest.raises(RuntimeError, match="not confirmed"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert article.goto_calls == []
    assert fake.reload_calls == []


def test_studio_reply_can_appear_after_delay_without_reload():
    node = make_node(0)
    fake, page, article = make_publication_page(node)
    def hydrate(timeout_ms):
        if timeout_ms == dzen_page._STUDIO_CONFIRM_DELAY_MS:
            node.published_replies.append({"author": "Configured Bot", "text": "мой ответ"})
    fake.on_wait_timeout = hydrate

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert node.reply_submit.clicks == 1
    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_public_article_expands_hidden_replies_and_loads_more_comments():
    node = make_node(0)
    source = public_root_for(node)
    source.hidden_replies = source.replies
    source.replies = []
    unrelated = FakePublicRoot(author="other", author_href="/user/other", text="other")
    article = FakeArticlePage(public_roots=[unrelated], hidden_roots=[source])
    fake, page, _ = make_publication_page(node, article=article)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "мой ответ"}
    )

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert article.more_button.clicks == 1
    assert source.expand_button.clicks == 1
    assert article.close_calls == 1
    assert fake.reload_calls == []


@pytest.mark.parametrize("source_present", [True, False])
def test_public_article_loads_all_batches_until_source_found_or_exhausted(source_present):
    class PagedArticlePage(FakeArticlePage):
        def __init__(self, batches):
            super().__init__(
                public_roots=[
                    FakePublicRoot(author="other", author_href="/user/other", text="other")
                ]
            )
            self.batches = batches

        def _load_more(self):
            self.public_roots.extend(self.batches.pop(0))

        def query_selector(self, selector):
            if selector == selectors.ARTICLE_MORE_COMMENTS and self.batches:
                return self.more_button
            return super().query_selector(selector)

    node = make_node(0)
    source = public_root_for(node, reply_text="long-tail reply")
    unrelated_batches = [
        [FakePublicRoot(author=f"other{i}", author_href=f"/user/other{i}", text=f"other{i}")]
        for i in range(27)
    ]
    final_batch = [source] if source_present else [
        FakePublicRoot(author="last", author_href="/user/last", text="last")
    ]
    article = PagedArticlePage([*unrelated_batches, final_batch])
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")

    comment = page.fetch_comments()[0]
    if source_present:
        assert page._verify_public_reply(
            comment, "long-tail reply", "/user/u0", "author0"
        )
    else:
        with pytest.raises(RuntimeError, match="source comment not found"):
            page._verify_public_reply(
                comment, "long-tail reply", "/user/u0", "author0"
            )
    assert article.more_button.clicks == 28
    assert article.batches == []
    assert article.close_calls == 1


def test_public_article_stalled_load_more_has_bounded_wait():
    node = make_node(0)
    article = FakeArticlePage(
        public_roots=[FakePublicRoot(author="other", author_href="/user/other", text="other")],
        hidden_roots=[public_root_for(node)],
    )
    article.more_button.on_click = lambda: None
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")

    with pytest.raises(RuntimeError, match="public article comments did not expand"):
        page._verify_public_reply(page.fetch_comments()[0], "reply", "/user/u0", "author0")

    assert article.more_button.clicks == 1
    assert article.waited_ms == [dzen_page._PUBLIC_COMMENT_WAIT_MS] * dzen_page._PUBLIC_COMMENT_POLL_LIMIT
    assert article.close_calls == 1


def test_public_article_empty_comment_list_has_bounded_wait():
    node = make_node(0)
    article = FakeArticlePage()
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")

    with pytest.raises(RuntimeError, match="source comment not found"):
        page._verify_public_reply(page.fetch_comments()[0], "reply", "/user/u0", "author0")

    assert article.more_button.clicks == 0
    assert article.waited_ms == [dzen_page._PUBLIC_COMMENT_WAIT_MS] * (dzen_page._PUBLIC_COMMENT_POLL_LIMIT - 1)
    assert article.close_calls == 1


def test_public_article_requires_source_author_even_when_text_matches():
    node = make_node(0)
    wrong_source = FakePublicRoot(
        author="author0", author_href="/user/not-the-source",
        text="text0", replies=[{"author": "Configured Bot", "text": "мой ответ"}]
    )
    article = FakeArticlePage(public_roots=[wrong_source])
    fake, page, _ = make_publication_page(node, article=article)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "мой ответ"}
    )

    with pytest.raises(RuntimeError, match="source_comment_not_found"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_public_article_finds_original_comment_inside_child_replies():
    node = make_node(0)
    parent = FakePublicRoot(
        author="parent", author_href="/user/parent", text="parent text",
        hidden_replies=[
            {"author": "author0", "authorHref": "/user/u0", "text": "text0"},
            {"author": "Configured Bot", "text": "мой ответ", "addressee": "author0"},
        ],
    )
    article = FakeArticlePage(public_roots=[parent])
    fake, page, _ = make_publication_page(node, article=article)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "мой ответ"}
    )

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert parent.expand_button.clicks == 1
    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_public_article_requires_reply_from_bot_in_source_thread():
    node = make_node(0)
    source = public_root_for(node, reply_author="Another Author")
    sibling = FakePublicRoot(
        author="other", author_href="/user/other", text="other",
        replies=[{"author": "Configured Bot", "text": "мой ответ"}],
    )
    article = FakeArticlePage(public_roots=[source, sibling])
    fake, page, _ = make_publication_page(node, article=article)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "мой ответ"}
    )

    with pytest.raises(RuntimeError, match="not confirmed"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_public_article_requires_exact_reply_text(caplog):
    node = make_node(0)
    article = FakeArticlePage(public_roots=[public_root_for(node, reply_text="other")])
    fake, page, _ = make_publication_page(node, article=article)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "мой ответ"}
    )
    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="not confirmed"):
            page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert article.close_calls == 1
    assert fake.reload_calls == []
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_reply_check_completed"
        and getattr(record, "failure_reason", None) == "reply_not_confirmed"
    )
    assert record.failure_stage == "public_reply_confirmation"
    assert record.failure_type == "ReplyNotConfirmed"
    assert record.result == "not_found"
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    assert "мой ответ" not in serialized
    assert "author0" not in serialized


def test_preexisting_studio_reply_still_requires_public_verification():
    node = make_node(0)
    node.published_replies.append({"author": "Configured Bot", "text": "мой ответ"})
    article = FakeArticlePage(public_roots=[public_root_for(node, reply_text="other")])
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    with pytest.raises(RuntimeError, match="not confirmed"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert node.reply_button.clicks == node.reply_submit.clicks == 0
    assert article.close_calls == 1


def test_preexisting_studio_and_public_reply_skips_duplicate_send():
    node = make_node(0)
    node.published_replies.append({"author": "Configured Bot", "text": "мой ответ"})
    fake, page, article = make_publication_page(node)
    fake.context.article_pages = [article]

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert node.reply_button.clicks == node.reply_submit.clicks == 0
    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_public_reply_preflight_prevents_duplicate_when_studio_loses_reply():
    node = make_node(0)
    fake, page, article = make_publication_page(node)
    fake.context.article_pages = [article]
    comment = page.fetch_comments()[0]

    page.publish_reply(comment, "мой ответ", auto_publish=True, reply_id=73, before_submit=lambda: None)

    assert node.reply_button.clicks == 0
    assert node.reply_submit.clicks == 0
    assert fake.listeners == {}
    assert article.goto_calls == [("https://dzen.ru/a/post1", "commit")]
    assert article.goto_timeouts == [dzen_page._PUBLIC_NAVIGATION_TIMEOUT_MS]
    assert article.close_calls == 1
    assert fake.reload_calls == []


def test_public_preflight_waits_for_delayed_reply_before_submit():
    node = make_node(0)
    public_root = public_root_for(node)
    public_root.replies.clear()
    article = FakeArticlePage(public_roots=[public_root])
    elapsed_ms = 0

    def reveal_delayed_reply(timeout_ms: int) -> None:
        nonlocal elapsed_ms
        elapsed_ms += timeout_ms
        if elapsed_ms > 2_000 and not public_root.replies:
            public_root.replies.append(
                {"author": "Configured Bot", "text": "мой ответ"}
            )

    article.wait_for_timeout = reveal_delayed_reply
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert elapsed_ms > 2_000
    assert elapsed_ms <= dzen_page._PUBLIC_PREFLIGHT_POLL_LIMIT * dzen_page._PUBLIC_COMMENT_WAIT_MS
    assert node.reply_button.clicks == node.reply_submit.clicks == 0


@pytest.mark.parametrize("failure_mode", ["missing_root", "read_error"])
def test_public_preflight_failure_polls_stay_bounded(failure_mode):
    node = make_node(0)
    public_root = public_root_for(node)
    public_root.replies.clear()
    article = FakeArticlePage(public_roots=[public_root])
    original_evaluate = public_root.evaluate
    read_count = 0

    def evaluate_with_transient_failure(script: str):
        nonlocal read_count
        read_count += 1
        if failure_mode == "read_error" and read_count > 1:
            raise RuntimeError("transient public comment read failure")
        data = original_evaluate(script)
        if failure_mode == "missing_root" and read_count == 1:
            article.public_roots.clear()
        return data

    public_root.evaluate = evaluate_with_transient_failure
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    def stop_before_submit(*args, **kwargs):
        raise RuntimeError("stopped after preflight")

    page._submit_reply = stop_before_submit

    with pytest.raises(RuntimeError, match="stopped after preflight"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert sum(article.waited_ms) <= dzen_page._PUBLIC_PREFLIGHT_POLL_LIMIT * dzen_page._PUBLIC_COMMENT_WAIT_MS


def test_public_preflight_sorts_newest_to_find_fresh_reply():
    node = make_node(0)
    unrelated = FakePublicRoot(author="other", author_href="/user/other", text="old")
    article = FakeArticlePage(
        public_roots=[unrelated],
        newest_roots=[public_root_for(node)],
        sort_label="Сначала популярные",
    )
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert article.sort_button.clicks == 1
    assert article.newest_button.clicks == 1
    assert article.sort_label == "Сначала новые"
    assert article.more_button.clicks == 0
    assert node.reply_submit.clicks == 0
    assert article.close_calls == 1


def test_public_preflight_uses_load_more_when_newest_sort_is_unavailable():
    node = make_node(0)
    unrelated = FakePublicRoot(author="other", author_href="/user/other", text="old")
    article = FakeArticlePage(
        public_roots=[unrelated],
        hidden_roots=[public_root_for(node)],
        sort_label="Сначала популярные",
        newest_option_available=False,
    )
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert article.sort_button.clicks == 2
    assert article.newest_button.clicks == 0
    assert article.more_button.clicks == 1
    assert node.reply_submit.clicks == 0
    assert article.close_calls == 1


def test_public_reply_preflight_error_stops_before_any_repeat_submit(caplog):
    node = make_node(0)
    article = FakeArticlePage(
        goto_error=TimeoutError(
            "navigation timeout https://dzen.ru/a/post?token=private-token"
            "#private-fragment private comment text"
        )
    )
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="public article navigation failed") as exc_info:
            page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, reply_id=73, before_submit=lambda: None)

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert node.reply_button.clicks == 0
    assert node.reply_submit.clicks == 0
    assert fake.listeners == {}
    assert article.close_calls == 1
    assert fake.reload_calls == []
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_navigation_retry"
    )
    assert record.failure_stage == "public_article_navigation"
    assert record.failure_reason == "navigation_exception"
    assert record.failure_type == "TimeoutError"
    assert record.failure_description == "browser operation timed out"
    assert record.attempt == 1
    assert record.reply_id == 73
    assert not hasattr(record, "comment_id")
    serialized = StructuredFormatter().format(record)
    for secret in ("private-token", "private-fragment", "private comment text", "https://"):
        assert secret not in serialized


def test_public_reply_preflight_logs_comment_loading_timeout(caplog):
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    article = FakeArticlePage(comments_available=False)
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="public article comments did not load"):
            page.publish_reply(comment, "мой ответ", auto_publish=True, before_submit=lambda: None)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_comments_failed"
    )
    assert record.failure_stage == "public_comment_loading"
    assert record.failure_reason == "comments_not_loaded"
    assert record.failure_type == "WaitTimeout"
    assert record.wait_count == dzen_page._PUBLIC_COMMENT_POLL_LIMIT
    assert record.http_status is None
    assert article.close_calls == 1


def test_public_article_http_status_is_logged_without_stopping_publication(caplog):
    node = make_node(0)
    preflight = FakeArticlePage(
        public_roots=[
            FakePublicRoot(author="author0", author_href="/user/u0", text="text0")
        ],
        goto_status=503,
    )
    final_article = FakeArticlePage(
        public_roots=[public_root_for(node, reply_text="private reply")],
        goto_status=200,
    )
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.context.article_pages = [preflight, final_article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    node.reply_submit.hide_on_click = True
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "private reply"}
    )
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        page.publish_reply(comment, "private reply", auto_publish=True, reply_id=73, before_submit=lambda: None)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_navigation_failed"
    )
    assert record.failure_stage == "public_article_navigation"
    assert record.failure_reason == "http_status"
    assert record.http_status == 503
    assert record.navigation_result == "http_error"
    assert node.reply_submit.clicks == 1
    assert any(
        getattr(item, "event", None) == "publication_confirmed"
        for item in caplog.records
    )
    serialized = "\n".join(
        StructuredFormatter().format(item)
        for item in caplog.records
        if item.name == "dzen_commenter.dzen.page"
    )
    assert "private reply" not in serialized
    assert "/a/post1" not in serialized


def test_public_reply_preflight_logs_missing_source_comment(caplog):
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    article = FakeArticlePage(
        public_roots=[
            FakePublicRoot(
                author="private author",
                author_href="/user/private-author",
                text="private unrelated comment",
            )
        ]
    )
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="source comment not found in public article"):
            page.publish_reply(comment, "private reply text", auto_publish=True, reply_id=73, before_submit=lambda: None)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_source_missing"
    )
    assert record.failure_stage == "public_source_comment_search"
    assert record.failure_reason == "source_comment_not_found"
    assert record.failure_type == "SourceCommentUnavailableError"
    assert record.roots_checked == 1
    assert record.candidates_checked == 1
    assert record.load_more_click_count == 0
    assert record.reply_id == 73
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    for secret in ("private author", "private unrelated comment", "private reply text", "/user/private-author"):
        assert secret not in serialized
    assert not any(hasattr(record, "comment_id") for record in caplog.records)


def test_public_reply_preflight_logs_collapsed_branch_timeout(caplog):
    node = make_node(0)
    root = FakePublicRoot(
        author="other author",
        author_href="/user/other",
        text="other comment",
        hidden_replies=[
            {"author": "author0", "authorHref": "/user/u0", "text": "text0"}
        ],
    )
    root.expand_button.on_click = lambda: None
    fake = FakePage([FakeGroup("/a/post1", [node])])
    article = FakeArticlePage(public_roots=[root])
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="source comment not found in public article"):
            page.publish_reply(comment, "мой ответ", auto_publish=True, before_submit=lambda: None)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_branch_expansion_failed"
        and getattr(record, "failure_reason", None) == "replies_not_expanded"
    )
    assert record.failure_stage == "public_thread_expansion"
    assert record.failure_type == "ExpansionTimeout"
    assert record.branch_expansion_attempt_count == 1
    assert record.branch_expansion_count == 0
    assert record.wait_count == dzen_page._PUBLIC_COMMENT_POLL_LIMIT
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    for secret in ("other author", "other comment", "author0", "text0", "мой ответ"):
        assert secret not in serialized


def test_public_reply_preflight_logs_load_more_click_exception(caplog):
    node = make_node(0)
    unrelated = FakePublicRoot(
        author="private author",
        author_href="/user/private-author",
        text="private unrelated comment",
    )
    hidden = FakePublicRoot(author="other", author_href="/user/other", text="other")
    article = FakeArticlePage(public_roots=[unrelated], hidden_roots=[hidden])

    def fail_load_more():
        raise RuntimeError("load more failed for https://dzen.ru/a/x?token=private-token")

    article.more_button.on_click = fail_load_more
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="load more failed"):
            page.publish_reply(comment, "private reply", auto_publish=True, before_submit=lambda: None)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_more_comments_failed"
    )
    assert record.failure_stage == "public_comment_loading"
    assert record.failure_reason == "load_more_click_failed"
    assert record.failure_type == "RuntimeError"
    assert record.load_more_click_count == 0
    assert record.load_more_click_attempt_count == 1
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    for secret in ("private author", "private unrelated comment", "private reply", "private-token", "https://"):
        assert secret not in serialized


def test_public_reply_preflight_logs_load_more_timeout(caplog):
    node = make_node(0)
    unrelated = FakePublicRoot(author="other", author_href="/user/other", text="other")
    hidden = FakePublicRoot(author="author0", author_href="/user/u0", text="text0")
    article = FakeArticlePage(public_roots=[unrelated], hidden_roots=[hidden])
    article.more_button.on_click = lambda: None
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.context.article_pages = [article]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    comment = page.fetch_comments()[0]

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="public article comments did not expand"):
            page.publish_reply(comment, "мой ответ", auto_publish=True, before_submit=lambda: None)

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_article_more_comments_failed"
        and getattr(record, "failure_reason", None) == "additional_comments_not_loaded"
    )
    assert record.failure_stage == "public_comment_loading"
    assert record.failure_type == "WaitTimeout"
    assert record.load_more_click_count == 1
    assert record.load_more_click_attempt_count == 1
    assert record.wait_count == dzen_page._PUBLIC_COMMENT_POLL_LIMIT
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    for secret in ("author0", "text0", "мой ответ"):
        assert secret not in serialized


def test_public_preflight_retries_transient_article_navigation():
    node = make_node(0)
    article = FakeArticlePage(
        public_roots=[public_root_for(node)],
        goto_errors=[TimeoutError("transient navigation timeout"), None],
    )
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert article.goto_calls == [
        ("https://dzen.ru/a/post1", "commit"),
        ("https://dzen.ru/a/post1", "commit"),
    ]
    assert article.goto_timeouts == [dzen_page._PUBLIC_NAVIGATION_TIMEOUT_MS] * 2
    assert article.close_calls == 1
    assert node.reply_button.clicks == node.reply_submit.clicks == 0
    assert fake.listeners == {}


def test_public_preflight_navigation_retries_exhaust_before_submit():
    node = make_node(0)
    article = FakeArticlePage(goto_error=TimeoutError("article navigation timeout"))
    fake, page, _ = make_publication_page(node, article=article)
    fake.context.article_pages = [article]

    with pytest.raises(RuntimeError, match="public article navigation failed") as exc_info:
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert str(exc_info.value.__cause__) == "article navigation timeout"
    assert article.goto_calls == [
        ("https://dzen.ru/a/post1", "commit"),
        ("https://dzen.ru/a/post1", "commit"),
    ]
    assert article.goto_timeouts == [dzen_page._PUBLIC_NAVIGATION_TIMEOUT_MS] * 2
    assert article.close_calls == 1
    assert node.reply_button.clicks == node.reply_submit.clicks == 0
    assert fake.listeners == {}


def test_hidden_studio_reply_prevents_duplicate_send():
    node = make_node(0)
    node.hidden_replies.append({"author": "Configured Bot", "text": "мой ответ"})
    fake, page, article = make_publication_page(node)
    fake.context.article_pages = [article]
    page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.more_button.clicks == 1
    assert node.reply_submit.clicks == 0
    assert article.close_calls == 1


@pytest.mark.parametrize(
    "hidden_reply",
    [
        {"author": "Another Author", "text": "мой ответ"},
        {"author": "Configured Bot", "text": "different reply"},
    ],
)
def test_hidden_studio_reply_requires_author_and_text(hidden_reply):
    node = make_node(0)
    node.hidden_replies.append(hidden_reply)
    fake, page, article = make_publication_page(node)
    with pytest.raises(RuntimeError, match="not confirmed"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.more_button.clicks == 1
    assert node.reply_submit.clicks == 1
    assert article.goto_calls == []


def test_failed_studio_reply_expansion_stops_before_submit():
    node = make_node(0)
    node.hidden_replies.append({"author": "Configured Bot", "text": "мой ответ"})
    node.more_button.on_click = lambda: None
    fake, page, article = make_publication_page(node)
    with pytest.raises(RuntimeError, match="replies did not expand"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.more_button.clicks == 1
    assert node.reply_submit.clicks == 0
    assert article.goto_calls == []


def test_wrong_studio_thread_does_not_confirm_reply():
    node, sibling = make_node(0), make_node(1)
    sibling.published_replies.append({"author": "Configured Bot", "text": "мой ответ"})
    fake = FakePage([FakeGroup("/a/post1", [node, sibling])])
    fake.context.article_pages = [FakeArticlePage(public_roots=[FakePublicRoot(
        author="author0", author_href="/user/u0", text="text0"
    )])]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    with pytest.raises(RuntimeError, match="not confirmed"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.reply_submit.clicks == 1
    assert sibling.reply_submit.clicks == 0
    assert fake.reload_calls == []


def test_missing_studio_thread_wrapper_fails_before_submit():
    node = make_node(0)
    node.has_thread_wrapper = False
    fake, page, _ = make_publication_page(node)
    with pytest.raises(RuntimeError, match="uninspectable"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.reply_submit.clicks == 0


def test_blank_bot_author_fails_before_submit():
    node = make_node(0)
    fake, page, _ = make_publication_page(node, bot_name=" ")
    with pytest.raises(RuntimeError, match="uninspectable"):
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert node.reply_submit.clicks == 0


def test_public_article_failure_closes_temporary_tab():
    node = make_node(0)
    article = FakeArticlePage(goto_error=RuntimeError("article unavailable"))
    fake, page, _ = make_publication_page(node, article=article)
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Configured Bot", "text": "мой ответ"}
    )
    with pytest.raises(PublicationUnconfirmedError, match="public_article_navigation") as exc_info:
        page.publish_reply(page.fetch_comments()[0], "мой ответ", auto_publish=True, before_submit=lambda: None)
    assert str(exc_info.value.__cause__.__cause__) == "article unavailable"
    assert article.close_calls == 1
    assert fake.reload_calls == []



def test_publish_reply_fills_draft_and_waits_without_submitting():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    page.publish_reply(target, "мой ответ", auto_publish=False)

    assert node.reply_button.clicks == 1
    assert node.reply_input.filled == ["мой ответ"]
    assert node.reply_submit.clicks == 0
    assert fake.waited_ms == [dzen_page._REPLY_SEARCH_WAIT_MS] * 3 + [5_000]
    assert fake.listeners == {}


def test_publish_reply_unmatched_raises_lookup_error(caplog):
    groups = [FakeGroup("/a/post1", [make_node(0)])]
    fake = FakePage(groups)
    page = DzenStudioPage(fake)
    comment = Comment(
        id=None,
        dzen_comment_id="deadbeef-not-on-page",
        publication_id=0,
        author="a",
        text="t",
        parent_comment_id=None,
        posted_at=None,
        fetched_at=datetime.now(timezone.utc),
        status=CommentStatus.NEW,
    )
    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(LookupError) as error:
            page.publish_reply(comment, "ответ", auto_publish=True, reply_id=73, before_submit=lambda: None)
    assert isinstance(error.value, SourceCommentUnavailableError)
    assert len(fake.mouse.wheel_calls) == dzen_page._REPLY_SEARCH_MAX_SCROLLS
    assert fake.waited_ms == [dzen_page._REPLY_SEARCH_WAIT_MS] * dzen_page._REPLY_SEARCH_MAX_SCROLLS
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]
    search = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_source_comment_search_completed"
    )
    assert search.result == "not_found"
    assert search.failure_stage == "studio_source_comment_search"
    assert search.failure_reason == "source_comment_not_found"
    assert search.reply_id == 73
    assert search.scroll_attempt_count == dzen_page._REPLY_SEARCH_MAX_SCROLLS
    assert search.candidates_checked == dzen_page._REPLY_SEARCH_MAX_SCROLLS + 1
    failure = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_action_failed"
    )
    assert failure.failure_stage == "studio_source_comment_search"
    assert failure.failure_reason == "source_comment_not_found"
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    assert "deadbeef-not-on-page" not in serialized


def test_source_comment_lookup_exception_logs_safely_and_propagates(caplog):
    fake = FakePage([])

    def fail_lookup(_selector):
        raise RuntimeError(
            "locator lookup failed: https://dzen.ru/a/post?token=private-token#fragment"
        )

    fake.query_selector_all = fail_lookup
    page = DzenStudioPage(fake)
    comment = Comment(
        id=None,
        dzen_comment_id="synthetic-private-id",
        publication_id=0,
        author="private author",
        text="private comment text",
        parent_comment_id=None,
        posted_at=None,
        fetched_at=datetime.now(timezone.utc),
        status=CommentStatus.NEW,
    )

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="locator lookup failed") as exc_info:
            page.publish_reply(comment, "private reply", auto_publish=True, reply_id=73, before_submit=lambda: None)

    assert "private-token" in str(exc_info.value)
    assert fake.mouse.wheel_calls == []
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_source_comment_search_failed"
    )
    assert record.failure_stage == "studio_source_comment_search"
    assert record.failure_reason == "lookup_exception"
    assert record.failure_type == "RuntimeError"
    assert record.failure_description == "locator lookup failed"
    assert record.reply_id == 73
    serialized = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == "dzen_commenter.dzen.page"
    )
    for secret in (
        "private-comment",
        "private-token",
        "token=",
        "fragment",
        "private author",
        "private comment text",
        "private reply",
        "synthetic-private-id",
        "https://",
        "dzen.ru",
        "/a/post",
    ):
        assert secret not in serialized
    assert "locator lookup failed" in serialized


def test_source_comment_scrolling_exception_logs_safely_and_propagates(caplog):
    fake = FakePage([FakeGroup("/a/post1", [make_node(0)])])

    def fail_scroll(_delta_x, _delta_y):
        raise TimeoutError(
            "scroll timeout for private comment https://dzen.ru/a/post?token=private-token"
        )

    fake.mouse.wheel = fail_scroll
    page = DzenStudioPage(fake)
    comment = Comment(
        id=None,
        dzen_comment_id="synthetic-private-id",
        publication_id=0,
        author="private author",
        text="private comment text",
        parent_comment_id=None,
        posted_at=None,
        fetched_at=datetime.now(timezone.utc),
        status=CommentStatus.NEW,
    )

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(TimeoutError, match="scroll timeout") as exc_info:
            page.publish_reply(comment, "private reply", auto_publish=True, reply_id=74, before_submit=lambda: None)

    assert "private-token" in str(exc_info.value)
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "publication_source_comment_search_failed"
    )
    assert record.failure_stage == "studio_source_comment_search"
    assert record.failure_reason == "scroll_exception"
    assert record.failure_type == "TimeoutError"
    assert record.failure_description == "browser operation timed out"
    assert record.scroll_attempt_count == 1
    assert record.candidates_checked == 1
    assert record.reply_id == 74
    serialized = "\n".join(
        StructuredFormatter().format(item)
        for item in caplog.records
        if item.name == "dzen_commenter.dzen.page"
    )
    for secret in (
        "private comment",
        "private-token",
        "private author",
        "private comment text",
        "private reply",
        "synthetic-private-id",
        "https://",
    ):
        assert secret not in serialized


def test_find_comment_after_three_stalled_scrolls_restores_page_top():
    initial_group = FakeGroup("/a/post1", [make_node(0)])
    target_node = make_node(1)
    fake = FakePage(
        [initial_group],
        scroll_groups=[
            [initial_group],
            [initial_group],
            [initial_group],
            [FakeGroup("/a/post1", [target_node])],
        ],
    )
    page = DzenStudioPage(fake)

    found = page._find_comment_node_with_scroll(
        synthetic_id("/a/post1", "/user/u1", "text1")
    )

    assert found is target_node
    assert len(fake.mouse.wheel_calls) == 4
    assert fake.waited_ms == [dzen_page._REPLY_SEARCH_WAIT_MS] * 4
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]


def test_find_target_child_after_scroll_and_hidden_reply_expansion():
    target_node = make_node(1)
    parent_node = make_node(0)
    parent_node.hidden_comment_children = [target_node]
    fake = FakePage(
        [FakeGroup("/a/post1", [make_node(2)])],
        scroll_groups=[[FakeGroup("/a/post1", [parent_node])]],
    )
    page = DzenStudioPage(fake)

    found = page._find_comment_node_with_scroll(
        synthetic_id("/a/post1", "/user/u1", "text1")
    )

    assert found is target_node
    assert parent_node.reply_more_button.clicks == 1
    assert len(fake.mouse.wheel_calls) == 1
    assert fake.waited_ms == [
        dzen_page._REPLY_SEARCH_WAIT_MS,
        dzen_page._REPLY_EXPANSION_POST_CLICK_WAIT_MS,
    ]
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]


def test_publish_reply_finds_target_loaded_after_scroll_and_restores_page_top():
    target_node = make_node(1)
    fake = FakePage(
        [FakeGroup("/a/post1", [make_node(0)])],
        scroll_groups=[[FakeGroup("/a/post1", [target_node])]],
    )
    fake.context.article_pages = [
        FakeArticlePage(public_roots=[FakePublicRoot(
            author="author1", author_href="/user/u1", text="text1"
        )]),
        FakeArticlePage(public_roots=[public_root_for(target_node, reply_text="готовый ответ")])
    ]
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    target = Comment(
        id=None,
        dzen_comment_id=synthetic_id("/a/post1", "/user/u1", "text1"),
        publication_id=0,
        author="author1",
        text="text1",
        parent_comment_id=None,
        posted_at=None,
        fetched_at=datetime.now(timezone.utc),
        status=CommentStatus.NEW,
        post_url="https://dzen.ru/a/post1",
    )
    target_node.reply_submit.on_click = lambda: target_node.published_replies.append(
        {"author": "Configured Bot", "text": "готовый ответ"}
    )

    page.publish_reply(target, "готовый ответ", auto_publish=True, before_submit=lambda: None)

    assert len(fake.mouse.wheel_calls) == 1
    assert dzen_page._STUDIO_CONFIRM_DELAY_MS in fake.waited_ms
    assert target_node.reply_input.filled == ["готовый ответ"]
    assert target_node.reply_submit.clicks == 1
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]
    assert fake.reload_calls == []


def test_publish_reply_stops_after_twenty_scrolls_with_new_comments():
    fake = FakePage(
        [FakeGroup("/a/post1", [make_node(0)])],
        scroll_groups=[
            [FakeGroup("/a/post1", [make_node(index)])]
            for index in range(1, 21)
        ],
    )
    page = DzenStudioPage(fake)
    comment = Comment(
        id=None,
        dzen_comment_id="deadbeef-not-on-page",
        publication_id=0,
        author="a",
        text="t",
        parent_comment_id=None,
        posted_at=None,
        fetched_at=datetime.now(timezone.utc),
        status=CommentStatus.NEW,
    )

    with pytest.raises(LookupError):
        page.publish_reply(comment, "ответ", auto_publish=True, before_submit=lambda: None)

    assert len(fake.mouse.wheel_calls) == dzen_page._REPLY_SEARCH_MAX_SCROLLS
    assert fake.waited_ms == [dzen_page._REPLY_SEARCH_WAIT_MS] * dzen_page._REPLY_SEARCH_MAX_SCROLLS


def test_publish_reply_keeps_lookup_error_when_scroll_cleanup_fails():
    fake = FakePage(
        [FakeGroup("/a/post1", [make_node(0)])],
        cleanup_error=RuntimeError("cleanup failed"),
    )
    page = DzenStudioPage(fake)
    comment = Comment(
        id=None,
        dzen_comment_id="deadbeef-not-on-page",
        publication_id=0,
        author="a",
        text="t",
        parent_comment_id=None,
        posted_at=None,
        fetched_at=datetime.now(timezone.utc),
        status=CommentStatus.NEW,
    )

    with pytest.raises(LookupError):
        page.publish_reply(comment, "ответ", auto_publish=True, before_submit=lambda: None)

    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]


# Acceptance 8 — пустая страница.
def test_fetch_comments_empty_page(caplog):
    page = DzenStudioPage(FakePage([]))
    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        assert page.fetch_comments() == []
    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "studio_comments_read_completed"
    )
    assert record.publication_card_count == 0
    assert record.comments_extracted == 0
    assert record.skipped_missing_link_count == 0


def test_fetch_comments_logs_studio_feed_read_failure(caplog):
    fake = FakePage([])

    def fail_read(_selector):
        raise RuntimeError("private feed text https://dzen.ru/studio?token=private-token")

    fake.query_selector_all = fail_read
    page = DzenStudioPage(fake)

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        with pytest.raises(RuntimeError, match="private feed text"):
            page.fetch_comments()

    record = next(
        record for record in caplog.records
        if getattr(record, "event", None) == "studio_comments_read_failed"
    )
    assert record.failure_stage == "studio_feed_read"
    assert record.failure_reason == "page_read_failed"
    assert record.failure_type == "RuntimeError"
    assert record.comments_extracted == 0
    serialized = StructuredFormatter().format(record)
    assert "private feed text" not in serialized
    assert "private-token" not in serialized
    assert "https://" not in serialized


def test_fetch_comments_uses_page_replaced_after_browser_restart():
    stale_browser = FakePage([])
    recovered_browser = FakePage([FakeGroup("/a/recovered", [make_node(0)])])
    current_browser = {"page": stale_browser}
    page = DzenStudioPage(lambda: current_browser["page"])

    current_browser["page"] = recovered_browser

    assert [comment.post_url for comment in page.fetch_comments()] == [
        "https://dzen.ru/a/recovered"
    ]


# Acceptance 09.1 — post_url каждого Comment равен post_href своей группы.
def test_fetch_comments_sets_post_url_per_group():
    groups = [
        FakeGroup("/a/post1", [make_node(0), make_node(1)]),
        FakeGroup("/a/post2", [make_node(2)]),
    ]
    page = DzenStudioPage(FakePage(groups))

    comments = page.fetch_comments()

    assert [c.post_url for c in comments] == [
        "https://dzen.ru/a/post1",
        "https://dzen.ru/a/post1",
        "https://dzen.ru/a/post2",
    ]


def test_fetch_comments_skips_group_without_a_resolvable_post_link():
    """A failed link extraction must not fabricate a comment with an unstable id:
    dzen_comment_id hashes post_href, so processing it with an empty href here
    would mint a different id than a later successful scrape of the same real
    comment — a phantom duplicate with no post_url, and a duplicate reply."""
    groups = [
        FakeGroup("", [make_node(0)]),
        FakeGroup("/a/post2", [make_node(1)]),
    ]
    page = DzenStudioPage(FakePage(groups))

    comments = page.fetch_comments()

    assert [c.post_url for c in comments] == ["https://dzen.ru/a/post2"]


def test_fetch_comments_logs_safe_read_summary(caplog):
    groups = [
        FakeGroup("", [make_node(1)], title="private article title"),
        FakeGroup("/a/post?token=private-token", [make_node(0)]),
    ]
    page = DzenStudioPage(FakePage(groups))

    with caplog.at_level(logging.INFO, logger="dzen_commenter.dzen.page"):
        page.fetch_comments()

    records = [
        record for record in caplog.records
        if getattr(record, "event", None) == "studio_comments_read_completed"
    ]
    assert len(records) == 1
    assert records[0].publication_card_count == 2
    assert records[0].comments_extracted == 1
    assert records[0].skipped_missing_link_count == 1
    serialized = StructuredFormatter().format(records[0])
    for secret in ("private article title", "private-token", "author0", "text0"):
        assert secret not in serialized


def test_fetch_comments_accepts_absolute_post_href():
    page = DzenStudioPage(
        FakePage([FakeGroup("https://dzen.ru/a/absolute-post", [make_node(0)])])
    )

    assert page.fetch_comments()[0].post_url == "https://dzen.ru/a/absolute-post"


def test_fetch_comments_accepts_video_post_href():
    page = DzenStudioPage(
        FakePage([FakeGroup("/video/watch/6a4628a617f7ac487bdc5899", [make_node(0)])])
    )

    assert (
        page.fetch_comments()[0].post_url
        == "https://dzen.ru/video/watch/6a4628a617f7ac487bdc5899"
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://dzen.ru/video/watch/vid1", True),
        ("https://dzen.ru/a/post1", False),
        (None, False),
        ("", False),
    ],
)
def test_is_video_post_url(url, expected):
    assert is_video_post_url(url) is expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/a/fallback-post", "https://dzen.ru/a/fallback-post"),
        ("https://dzen.ru/a/fallback-post", "https://dzen.ru/a/fallback-post"),
    ],
)
def test_fetch_comments_uses_safe_fallback_post_link(href, expected):
    class FallbackGroup(FakeGroup):
        def query_selector(self, selector: str):
            if selector == selectors.POST_LINK:
                return None
            if selector == selectors.POST_LINK_FALLBACK:
                return self._post_link
            return super().query_selector(selector)

    page = DzenStudioPage(FakePage([FallbackGroup(href, [make_node(0)])]))

    assert page.fetch_comments()[0].post_url == expected


def test_fetch_comments_sets_publication_title_per_group():
    page = DzenStudioPage(
        FakePage([FakeGroup("/a/post1", [make_node(0)], title="  Заголовок  ")])
    )

    assert page.fetch_comments()[0].publication_title == "Заголовок"


def test_fetch_comments_keeps_relative_path_for_id_and_saves_prior_dialogue():
    groups = [FakeGroup("/a/post1", [make_node(0), make_node(1)])]
    page = DzenStudioPage(FakePage(groups))

    first, second = page.fetch_comments()

    assert first.thread_text == ""
    assert second.thread_text == "author0: text0"
    assert second.dzen_comment_id == synthetic_id("/a/post1", "/user/u1", "text1")


def test_synthetic_id_matches_helper():
    node = make_node(5)
    page = DzenStudioPage(FakePage([FakeGroup("/a/postX", [node])]))
    comment = page.fetch_comments()[0]
    assert comment.dzen_comment_id == synthetic_id("/a/postX", "/user/u5", "text5")
