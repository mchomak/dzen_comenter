import inspect
from datetime import datetime, timedelta, timezone

import pytest

import dzen_commenter.dzen  # noqa: F401
from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.errors import SourceCommentUnavailableError
from dzen_commenter.contracts.interfaces import DzenPage
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import DzenStudioPage, selectors
from dzen_commenter.dzen.page import is_video_post_url, synthetic_id


class FakeText:
    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


class FakeButton:
    def __init__(self, on_click=None) -> None:
        self.clicks = 0
        self.on_click = on_click

    def click(self) -> None:
        self.clicks += 1
        if self.on_click is not None:
            self.on_click()


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
        self.reply_submit = FakeButton()
        self.published_replies: list[dict[str, str]] = []
        self.hidden_replies: list[dict[str, str]] = []
        self.more_button = FakeButton(
            lambda: self.published_replies.extend(self.hidden_replies)
        )
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
        return self._children.get(selector)

    def evaluate(self, script: str, arg=None):
        if "authorHref:" in script:
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
            return list(self._nodes)
        return []


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


class FakeArticlePage:
    def __init__(
        self,
        *,
        article_text: str = "",
        article_content: dict | None = None,
        goto_error: Exception | None = None,
    ) -> None:
        self.article_text = article_text
        self.article_content = article_content
        self.goto_error = goto_error
        self.goto_calls: list[tuple[str, str]] = []
        self.close_calls = 0

    def goto(self, url: str, *, wait_until: str) -> None:
        self.goto_calls.append((url, wait_until))
        if self.goto_error is not None:
            raise self.goto_error

    def query_selector(self, selector: str):
        if selector == "article" and self.article_text:
            return FakeText(self.article_text)
        return None

    def evaluate(self, _script: str):
        return self.article_content

    def close(self) -> None:
        self.close_calls += 1


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


def test_fetch_article_text_uses_article_body_and_closes_temporary_tab():
    browser = FakePage([FakeGroup("/a/post", [])])
    article_page = FakeArticlePage(article_text="Article body")
    browser.context.article_pages = [article_page]
    page = DzenStudioPage(browser)

    assert page.fetch_article_text("https://dzen.ru/a/post") == "Article body"
    assert article_page.goto_calls == [("https://dzen.ru/a/post", "domcontentloaded")]
    assert article_page.close_calls == 1


def test_fetch_article_text_does_not_fall_back_to_the_page_main_element():
    class MainOnlyArticlePage(FakeArticlePage):
        def query_selector(self, selector: str):
            if selector == "main":
                return FakeText("Page chrome and recommendations")
            return None

    browser = FakePage([FakeGroup("/a/post", [])])
    article_page = MainOnlyArticlePage()
    browser.context.article_pages = [article_page]
    page = DzenStudioPage(browser)

    assert page.fetch_article_text("https://dzen.ru/a/post") is None
    assert article_page.close_calls == 1


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


# Acceptance 7 — publish_reply находит нужный узел.
def test_publish_reply_targets_matching_node():
    node0 = make_node(0)
    node1 = make_node(1)
    groups = [FakeGroup("/a/post1", [node0, node1])]
    fake = FakePage(groups)
    page = DzenStudioPage(fake)

    target = page.fetch_comments()[1]  # соответствует node1
    node1.reply_submit.on_click = lambda: node1.published_replies.append(
        {"author": "Екатерина Великая", "text": "мой ответ"}
    )
    page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node1.reply_button.clicks == 1
    assert node1.reply_input.filled == ["мой ответ"]
    assert node1.reply_submit.clicks == 1
    assert node0.reply_button.clicks == 0
    assert node0.reply_input.filled == []
    assert node0.reply_submit.clicks == 0
    assert fake.mouse.wheel_calls == []
    assert fake.evaluate_calls == []
    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert fake.listeners == {"request": [], "response": []}


def test_auto_publish_is_not_confirmed_by_a_successful_click_alone():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError, match="post-reload verification"):
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node.reply_submit.clicks == 1
    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]


def test_auto_publish_waits_for_delayed_acknowledgment_before_reloading():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.on_wait_timeout = lambda _timeout_ms: node.published_replies.append(
        {"author": "Екатерина Великая", "text": "мой ответ"}
    )
    reload_saw_acknowledgment = []
    fake.on_reload = lambda: reload_saw_acknowledgment.append(
        bool(node.published_replies)
    )
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    page.publish_reply(target, "мой ответ", auto_publish=True)

    assert reload_saw_acknowledgment == [True]


def test_auto_publish_recovers_reply_visible_only_after_reload():
    node = make_node(0)
    reloaded_node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])

    def reload_with_persisted_reply():
        reloaded_node.published_replies.append(
            {"author": "Екатерина Великая", "text": "мой ответ"}
        )
        fake._groups = [FakeGroup("/a/post1", [reloaded_node])]

    fake.on_reload = reload_with_persisted_reply
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node.reply_submit.clicks == 1
    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]


def test_auto_publish_accepts_matching_create_response_when_reload_hides_reply():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]
    reply_text = "мой  ответ"

    class Request:
        method = "POST"
        url = "https://dzen.ru/api/comments/create?private=secret"
        post_data_json = {
            "text": reply_text,
            "publisherId": "publisher-secret",
            "documentId": "document-secret",
            "rootId": "root-secret",
            "replyToId": "reply-secret",
        }

    class Response:
        request = Request()
        status = 200

        def json(self):
            return {
                "status": "ok",
                "comments": [{
                    "id": "created-secret",
                    "text": "мой ответ",
                    "publisherId": "publisher-secret",
                    "documentId": "document-secret",
                    "rootId": "root-secret",
                    "replyToId": "reply-secret",
                    "visibility": "visible",
                    "asPublisher": False,
                }],
            }

    def submit():
        fake.emit("request", Response.request)
        fake.emit("response", Response())
        node.published_replies.append(
            {"author": "Екатерина Великая", "text": reply_text}
        )

    node.reply_submit.on_click = submit
    fake.on_reload = node.published_replies.clear

    page.publish_reply(target, reply_text, auto_publish=True)

    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert node.published_replies == []
    assert node.reply_submit.clicks == 1
    assert fake.listeners == {"request": [], "response": []}


@pytest.mark.parametrize(
    "response_status,response_body",
    [
        (403, {"status": "ok", "comments": []}),
        (200, {"status": "error", "comments": []}),
        (200, {"status": "ok", "comments": []}),
        (200, {"status": "ok", "comments": [{"id": "", "text": "мой ответ", "visibility": "visible"}]}),
        (200, {"status": "ok", "comments": [{"id": "id", "text": "wrong text", "visibility": "visible"}]}),
        (200, {"status": "ok", "comments": [{"id": "id", "text": "мой ответ", "visibility": "hidden"}]}),
        (200, {"status": "ok", "comments": [{"id": "id", "text": "мой ответ", "visibility": "visible", "replyToId": "wrong"}]}),
    ],
)
def test_auto_publish_rejects_bad_create_response_with_optimistic_dom(
    response_status, response_body
):
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    class Request:
        method = "POST"
        url = "https://dzen.ru/api/comments/create?private=secret"
        post_data_json = {
            "text": "мой ответ",
            "publisherId": "publisher-secret",
            "documentId": "document-secret",
            "rootId": "root-secret",
            "replyToId": "reply-secret",
        }

    class Response:
        request = Request()
        status = response_status

        def json(self):
            return response_body

    def submit():
        fake.emit("request", Response.request)
        fake.emit("response", Response())
        node.published_replies.append(
            {"author": "Екатерина Великая", "text": "мой ответ"}
        )

    node.reply_submit.on_click = submit
    fake.on_reload = node.published_replies.clear

    with pytest.raises(RuntimeError, match="post-reload verification") as exc_info:
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert "secret" not in str(exc_info.value)
    assert "мой ответ" not in str(exc_info.value)
    assert node.reply_submit.clicks == 1
    assert fake.listeners == {"request": [], "response": []}


@pytest.mark.parametrize(
    "mismatch_field",
    ["publisherId", "documentId", "rootId", "replyToId"],
)
def test_auto_publish_requires_create_response_in_original_thread(mismatch_field):
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]
    payload = {
        "text": "мой ответ",
        "publisherId": "publisher-secret",
        "documentId": "document-secret",
        "rootId": "root-secret",
        "replyToId": "reply-secret",
    }
    created = {
        **payload,
        "id": "created-secret",
        "visibility": "visible",
        mismatch_field: "unrelated-secret",
    }

    class Request:
        method = "POST"
        url = "https://dzen.ru/api/comments/create"
        post_data_json = payload

    class Response:
        request = Request()
        status = 200

        def json(self):
            return {"status": "ok", "comments": [created]}

    def submit():
        fake.emit("request", Response.request)
        fake.emit("response", Response())
        node.published_replies.append(
            {"author": "Екатерина Великая", "text": "мой ответ"}
        )

    node.reply_submit.on_click = submit
    fake.on_reload = node.published_replies.clear

    with pytest.raises(RuntimeError, match="post-reload verification") as exc_info:
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert "secret" not in str(exc_info.value)
    assert node.reply_submit.clicks == 1
    assert fake.listeners == {"request": [], "response": []}


def test_auto_publish_optimistic_reply_without_create_response_still_fails():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]
    node.reply_submit.on_click = lambda: node.published_replies.append(
        {"author": "Екатерина Великая", "text": "мой ответ"}
    )
    fake.on_reload = node.published_replies.clear

    with pytest.raises(RuntimeError, match="post-reload verification"):
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node.reply_submit.clicks == 1
    assert fake.listeners == {"request": [], "response": []}


def test_auto_publish_waits_for_create_response_before_reload():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    class Request:
        method = "POST"
        url = "https://dzen.ru/api/comments/create"
        post_data_json = {
            "text": "мой ответ",
            "rootId": "root-secret",
            "replyToId": "reply-secret",
        }

    class Response:
        request = Request()
        status = 200

        def json(self):
            return {
                "status": "ok",
                "comments": [{
                    **self.request.post_data_json,
                    "id": "created-secret",
                    "visibility": "visible",
                }],
            }

    def submit():
        fake.emit("request", Response.request)
        node.published_replies.append(
            {"author": "Екатерина Великая", "text": "мой ответ"}
        )

    node.reply_submit.on_click = submit
    emitted = False

    def deliver_response(_timeout_ms):
        nonlocal emitted
        if not emitted:
            assert fake.reload_calls == []
            fake.emit("response", Response())
            emitted = True

    fake.on_wait_timeout = deliver_response
    fake.on_reload = node.published_replies.clear

    page.publish_reply(target, "мой ответ", auto_publish=True)

    assert emitted
    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]
    assert node.reply_submit.clicks == 1
    assert fake.listeners == {"request": [], "response": []}


def test_auto_publish_fails_after_reload_when_reply_exists_only_in_sibling_thread():
    node = make_node(0)
    sibling = make_node(1)
    sibling.published_replies.append(
        {"author": "Екатерина Великая", "text": "мой ответ"}
    )
    fake = FakePage([FakeGroup("/a/post1", [node])])
    fake.on_reload = lambda: setattr(
        fake, "_groups", [FakeGroup("/a/post1", [node, sibling])]
    )
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError, match="post-reload verification") as exc_info:
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert "мой ответ" not in str(exc_info.value)
    assert "text0" not in str(exc_info.value)
    assert node.reply_submit.clicks == 1
    assert sibling.reply_submit.clicks == 0
    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]


def test_unconfirmed_reply_reports_sanitized_submit_outcome_and_cleans_listeners():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    class Request:
        method = "POST"
        url = "https://dzen.ru/api/comment/private-reply/private-token?token=private-query"
        post_data = "private-body"
        headers = {"Cookie": "private-cookie"}

    class Response:
        request = Request()
        status = 403

    def submit() -> None:
        fake.emit("request", Response.request)
        fake.emit("response", Response())
        pending = Request()
        pending.url = "https://api.dzen.ru/api/comment/pending?token=private-query"
        fake.emit("request", pending)
        for index in range(6):
            overflow = Request()
            overflow.url = f"https://dzen.ru/api/overflow/{index}?token=private-query"
            fake.emit("request", overflow)

    node.reply_submit.on_click = submit

    with pytest.raises(RuntimeError, match="post-reload verification") as exc_info:
        page.publish_reply(target, "private-reply", auto_publish=True)

    message = str(exc_info.value)
    assert "ack_before_reload=false" in message
    assert "source_found=true" in message
    assert "target_reply_count=0" in message
    assert "POST dzen.ru path_sha256=f356dc5d49b6 403" in message
    assert "pending_responses=4" in message
    assert "POST api.dzen.ru path_sha256=e335b7ee211c pending" in message
    assert message.count("POST ") == 5
    assert "truncated=true" in message
    assert "/api/overflow/5" not in message
    for secret in (
        "private-query",
        "private-body",
        "private-cookie",
        "private-reply",
        "private-token",
        "text0",
        "/api/comment/",
    ):
        assert secret not in message
    assert fake.listeners == {"request": [], "response": []}
    assert node.reply_submit.clicks == 1


def test_auto_publish_fails_before_submit_when_source_thread_wrapper_is_missing():
    node = make_node(0)
    node.has_thread_wrapper = False
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError) as exc_info:
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node.reply_button.clicks == 0
    assert node.reply_submit.clicks == 0
    assert fake.reload_calls == []
    assert "uninspectable" in str(exc_info.value)


def test_auto_publish_fails_before_submit_when_bot_author_is_blank():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "  ")
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError) as exc_info:
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node.reply_button.clicks == 0
    assert node.reply_submit.clicks == 0
    assert fake.reload_calls == []
    assert "uninspectable" in str(exc_info.value)


def test_auto_publish_skips_duplicate_when_matching_bot_reply_is_already_visible():
    node = make_node(0)
    node.published_replies.append(
        {"author": "Configured Bot", "text": "мой  ответ"}
    )
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(
        fake, bot_account_name_provider=lambda: "Configured Bot"
    )
    target = page.fetch_comments()[0]

    page.publish_reply(target, " мой ответ ", auto_publish=True)

    assert node.reply_button.clicks == 0
    assert node.reply_submit.clicks == 0
    assert fake.reload_calls == []


def test_auto_publish_expands_target_thread_before_resubmitting_hidden_reply():
    node = make_node(0)
    node.hidden_replies.append({"author": "Configured Bot", "text": "already posted"})
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    target = page.fetch_comments()[0]

    page.publish_reply(target, "already posted", auto_publish=True)

    assert node.more_button.clicks == 1
    assert node.reply_submit.clicks == 0
    assert fake.reload_calls == []


def test_auto_publish_confirms_hidden_reply_after_reload():
    node = make_node(0)
    reloaded_node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])

    def reload_with_collapsed_reply():
        reloaded_node.hidden_replies.append(
            {"author": "Configured Bot", "text": "posted reply"}
        )
        fake._groups = [FakeGroup("/a/post1", [reloaded_node])]

    fake.on_reload = reload_with_collapsed_reply
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    target = page.fetch_comments()[0]

    page.publish_reply(target, "posted reply", auto_publish=True)

    assert node.reply_submit.clicks == 1
    assert reloaded_node.more_button.clicks == 1
    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]


@pytest.mark.parametrize(
    "hidden_reply",
    [
        {"author": "Another Author", "text": "posted reply"},
        {"author": "Configured Bot", "text": "different reply"},
    ],
)
def test_hidden_reply_requires_exact_bot_author_and_text(hidden_reply):
    node = make_node(0)
    node.hidden_replies.append(hidden_reply)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError, match="post-reload verification"):
        page.publish_reply(target, "posted reply", auto_publish=True)

    assert node.more_button.clicks == 1
    assert node.reply_submit.clicks == 1


def test_hidden_reply_in_sibling_thread_does_not_confirm_target():
    node = make_node(0)
    sibling = make_node(1)
    sibling.hidden_replies.append(
        {"author": "Configured Bot", "text": "posted reply"}
    )
    fake = FakePage([FakeGroup("/a/post1", [node, sibling])])
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError, match="post-reload verification"):
        page.publish_reply(target, "posted reply", auto_publish=True)

    assert node.reply_submit.clicks == 1
    assert sibling.more_button.clicks == 0
    assert sibling.reply_submit.clicks == 0


def test_failed_thread_expansion_stops_before_submit():
    node = make_node(0)
    node.hidden_replies.append(
        {"author": "Configured Bot", "text": "posted reply"}
    )
    node.more_button.on_click = lambda: None
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake, bot_account_name_provider=lambda: "Configured Bot")
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError, match="replies did not expand"):
        page.publish_reply(target, "posted reply", auto_publish=True)

    assert node.more_button.clicks == 1
    assert node.reply_submit.clicks == 0


def test_same_reply_text_from_another_author_does_not_confirm_publication():
    node = make_node(0)
    node.published_replies.append({"author": "другой автор", "text": "мой ответ"})
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    with pytest.raises(RuntimeError, match="post-reload verification"):
        page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node.reply_submit.clicks == 1


def test_publish_reply_fills_draft_and_waits_without_submitting():
    node = make_node(0)
    fake = FakePage([FakeGroup("/a/post1", [node])])
    page = DzenStudioPage(fake)
    target = page.fetch_comments()[0]

    page.publish_reply(target, "мой ответ", auto_publish=False)

    assert node.reply_button.clicks == 1
    assert node.reply_input.filled == ["мой ответ"]
    assert node.reply_submit.clicks == 0
    assert fake.waited_ms == [5_000]
    assert fake.listeners == {}


def test_publish_reply_unmatched_raises_lookup_error():
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
    with pytest.raises(LookupError) as error:
        page.publish_reply(comment, "ответ", auto_publish=True)
    assert isinstance(error.value, SourceCommentUnavailableError)
    assert len(fake.mouse.wheel_calls) == 20
    assert fake.waited_ms == [500] * 20
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]


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
    assert fake.waited_ms == [500] * 4
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]


def test_publish_reply_finds_target_loaded_after_scroll_and_restores_page_top():
    target_node = make_node(1)
    fake = FakePage(
        [FakeGroup("/a/post1", [make_node(0)])],
        scroll_groups=[[FakeGroup("/a/post1", [target_node])]],
    )
    page = DzenStudioPage(fake)
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
    )
    target_node.reply_submit.on_click = lambda: target_node.published_replies.append(
        {"author": "Екатерина Великая", "text": "готовый ответ"}
    )

    page.publish_reply(target, "готовый ответ", auto_publish=True)

    assert len(fake.mouse.wheel_calls) == 1
    assert fake.waited_ms == [500] * 22
    assert target_node.reply_input.filled == ["готовый ответ"]
    assert target_node.reply_submit.clicks == 1
    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]
    assert fake.reload_calls == [{"wait_until": "domcontentloaded"}]


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
        page.publish_reply(comment, "ответ", auto_publish=True)

    assert len(fake.mouse.wheel_calls) == 20
    assert fake.waited_ms == [500] * 20


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
        page.publish_reply(comment, "ответ", auto_publish=True)

    assert fake.evaluate_calls == ["window.scrollTo(0, 0)"]


# Acceptance 8 — пустая страница.
def test_fetch_comments_empty_page():
    page = DzenStudioPage(FakePage([]))
    assert page.fetch_comments() == []


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
