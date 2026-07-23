import inspect
from datetime import datetime, timedelta, timezone

import pytest

import dzen_commenter.dzen  # noqa: F401
from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.interfaces import DzenPage
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import DzenStudioPage, selectors
from dzen_commenter.dzen.page import synthetic_id


class FakeText:
    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


class FakeButton:
    def __init__(self) -> None:
        self.clicks = 0

    def click(self) -> None:
        self.clicks += 1


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


class FakeGroup:
    def __init__(
        self, post_href: str, nodes: list[FakeCommentNode], title: str | None = None
    ) -> None:
        self._post_link = FakeLink(post_href)
        self._nodes = nodes
        self._title = FakeText(title) if title is not None else None

    def query_selector(self, selector: str):
        if selector == selectors.POST_LINK:
            return self._post_link
        if selector == selectors.POST_TITLE:
            return self._title
        return None

    def query_selector_all(self, selector: str):
        if selector == selectors.COMMENT_NODE:
            return list(self._nodes)
        return []


class FakePage:
    def __init__(self, groups: list[FakeGroup]) -> None:
        self._groups = groups
        self.waited_ms: list[float] = []

    def query_selector_all(self, selector: str):
        if selector == selectors.POST_GROUP:
            return list(self._groups)
        return []

    def wait_for_timeout(self, timeout_ms: float) -> None:
        self.waited_ms.append(timeout_ms)


def make_node(i: int, date: str | None = None) -> FakeCommentNode:
    return FakeCommentNode(
        author_href=f"/user/u{i}",
        author=f"author{i}",
        text=f"text{i}",
        date=date,
    )


# Acceptance 2 — структурное соответствие контракту DzenPage.
def test_implements_dzen_page_contract():
    for name in ("fetch_comments", "publish_reply"):
        proto_sig = inspect.signature(getattr(DzenPage, name))
        impl_sig = inspect.signature(getattr(DzenStudioPage, name))
        assert list(proto_sig.parameters) == list(impl_sig.parameters)


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
        assert c.fetched_at.tzinfo is not None
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
    page.publish_reply(target, "мой ответ", auto_publish=True)

    assert node1.reply_button.clicks == 1
    assert node1.reply_input.filled == ["мой ответ"]
    assert node1.reply_submit.clicks == 1
    assert node0.reply_button.clicks == 0
    assert node0.reply_input.filled == []
    assert node0.reply_submit.clicks == 0


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


def test_publish_reply_unmatched_raises_lookup_error():
    groups = [FakeGroup("/a/post1", [make_node(0)])]
    page = DzenStudioPage(FakePage(groups))
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


# Acceptance 8 — пустая страница.
def test_fetch_comments_empty_page():
    page = DzenStudioPage(FakePage([]))
    assert page.fetch_comments() == []


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
