import inspect
from datetime import datetime, timezone

import dzen_commenter.dzen  # noqa: F401
from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.interfaces import DzenPage
from dzen_commenter.contracts.models import Comment
from dzen_commenter.dzen import DzenStudioPage, selectors


class FakeChild:
    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


class FakeElement:
    def __init__(self, *, attrs: dict, author: str, text: str) -> None:
        self._attrs = attrs
        self._children = {
            selectors.COMMENT_AUTHOR: FakeChild(author),
            selectors.COMMENT_TEXT: FakeChild(text),
        }

    def get_attribute(self, name: str):
        return self._attrs.get(name)

    def query_selector(self, selector: str):
        return self._children.get(selector)


class FakePage:
    def __init__(self, elements) -> None:
        self._elements = elements
        self.fill_calls: list[tuple[str, str]] = []
        self.click_calls: list[str] = []

    def query_selector_all(self, selector: str):
        if selector == selectors.COMMENT_ITEM:
            return list(self._elements)
        return []

    def fill(self, selector: str, value: str) -> None:
        self.fill_calls.append((selector, value))

    def click(self, selector: str) -> None:
        self.click_calls.append(selector)


def make_element(i: int) -> FakeElement:
    return FakeElement(
        attrs={
            selectors.COMMENT_ID_ATTR: f"c{i}",
            selectors.COMMENT_PARENT_ATTR: None,
            selectors.COMMENT_POSTED_ATTR: "2026-06-19T10:00:00+00:00",
        },
        author=f"author{i}",
        text=f"text{i}",
    )


# Acceptance 2 — структурное соответствие контракту DzenPage.
def test_implements_dzen_page_contract():
    for name in ("fetch_comments", "publish_reply"):
        proto_sig = inspect.signature(getattr(DzenPage, name))
        impl_sig = inspect.signature(getattr(DzenStudioPage, name))
        assert list(proto_sig.parameters) == list(impl_sig.parameters)


# Acceptance 8 — fetch_comments() парсит в доменные Comment.
def test_fetch_comments_parses_into_domain_comments():
    n = 3
    elements = [make_element(i) for i in range(n)]
    page = DzenStudioPage(FakePage(elements))

    comments = page.fetch_comments()

    assert isinstance(comments, list)
    assert len(comments) == n
    for i, c in enumerate(comments):
        assert isinstance(c, Comment)
        assert c.status == CommentStatus.NEW
        assert c.id is None
        assert c.publication_id == 0
        assert isinstance(c.fetched_at, datetime)
        assert c.fetched_at.tzinfo is not None
        assert c.fetched_at.utcoffset() is not None
        assert c.dzen_comment_id == f"c{i}"
        assert c.author == f"author{i}"
        assert c.text == f"text{i}"


def test_fetch_comments_empty_page():
    page = DzenStudioPage(FakePage([]))
    assert page.fetch_comments() == []


# Acceptance 9 — publish_reply() заполняет поле и кликает submit.
def test_publish_reply_fills_and_clicks():
    fake = FakePage([])
    page = DzenStudioPage(fake)
    comment = Comment(
        id=None,
        dzen_comment_id="c1",
        publication_id=0,
        author="a",
        text="t",
        parent_comment_id=None,
        posted_at=None,
        fetched_at=datetime.now(timezone.utc),
        status=CommentStatus.NEW,
    )

    page.publish_reply(comment, "мой ответ")

    assert fake.fill_calls == [(selectors.REPLY_INPUT, "мой ответ")]
    assert fake.click_calls == [selectors.REPLY_SUBMIT]
