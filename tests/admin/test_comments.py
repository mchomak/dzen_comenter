import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from dzen_commenter.admin.app import create_app
from dzen_commenter.admin.config import AdminSettings
from dzen_commenter.admin.queries import (
    fetch_feed,
    fetch_status_counts,
    parse_thread_messages,
)
from dzen_commenter.db.models import Base, CommentTable, PublicationTable, ReplyTable

PASSWORD = "correct-horse-battery"


@pytest.fixture
def engine():
    """In-memory SQLite shared across connections/threads (StaticPool)."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(insert(PublicationTable).values(id=1, dzen_publication_id="p1"))
    return eng


def _add_comment(
    engine, *, cid, author, text, post_url, fetched_at, thread_text=None, post_title=None
):
    with engine.begin() as conn:
        conn.execute(
            insert(CommentTable).values(
                id=cid,
                dzen_comment_id=f"c-{cid}",
                publication_id=1,
                author=author,
                text=text,
                post_title=post_title,
                post_url=post_url,
                thread_text=thread_text,
                fetched_at=fetched_at,
                status="new",
            )
        )


def _add_reply(engine, *, rid, comment_id, generated_text, status, error_reason=None):
    with engine.begin() as conn:
        conn.execute(
            insert(ReplyTable).values(
                id=rid,
                comment_id=comment_id,
                generated_text=generated_text,
                status=status,
                error_reason=error_reason,
            )
        )


@pytest.fixture
def client(engine) -> TestClient:
    settings = AdminSettings(
        _env_file=None,
        ADMIN_PASSWORD=PASSWORD,
        ADMIN_SESSION_SECRET="test-session-secret",
    )
    app = create_app(settings, engine=engine)
    client = TestClient(app, follow_redirects=False)
    client.post("/login", data={"password": PASSWORD})
    return client


# --- parse_thread_messages unit ---


def test_parse_thread_messages_basic():
    assert parse_thread_messages("alice: hello\nbob: hi there") == [
        ("alice", "hello"),
        ("bob", "hi there"),
    ]


def test_parse_thread_messages_none_and_empty():
    assert parse_thread_messages(None) == []
    assert parse_thread_messages("") == []


def test_parse_thread_messages_no_separator():
    assert parse_thread_messages("just a line") == [("", "just a line")]


def test_parse_thread_messages_skips_blank_lines():
    assert parse_thread_messages("alice: hi\n\nbob: yo") == [
        ("alice", "hi"),
        ("bob", "yo"),
    ]


# --- fetch_feed unit ---


def test_feed_fresh_first_and_limit(engine):
    for i in range(1, 106):
        _add_comment(
            engine,
            cid=i,
            author=f"a{i}",
            text=f"t{i}",
            post_url=f"http://post/{i}",
            fetched_at=datetime(2026, 1, 1, 0, 0, i % 60, i),
        )
    # Make ordering unambiguous by fetched_at.
    with engine.begin() as conn:
        from sqlalchemy import update

        for i in range(1, 106):
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id == i)
                .values(fetched_at=datetime(2026, 1, 1, 12, 0, 0).replace(microsecond=i))
            )

    feed = fetch_feed(engine)
    assert len(feed) == 100  # limit
    # Freshest (largest microsecond) is comment id=105 → author a105 first.
    assert feed[0].author == "a105"
    # Strictly descending by fetched_at.
    times = [row.fetched_at for row in feed]
    assert times == sorted(times, reverse=True)


def test_feed_reports_reply_statuses(engine):
    _add_comment(
        engine, cid=1, author="alice", text="hi", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 3),
    )
    _add_comment(
        engine, cid=2, author="bob", text="yo", post_url="http://post/2",
        fetched_at=datetime(2026, 1, 1, 12, 0, 2),
    )
    _add_comment(
        engine, cid=3, author="carol", text="hey", post_url="http://post/3",
        fetched_at=datetime(2026, 1, 1, 12, 0, 1),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="pub reply", status="published")
    _add_reply(engine, rid=2, comment_id=2, generated_text="err reply", status="error",
               error_reason="rate limit")
    # comment 3 has no reply

    feed = {row.author: row for row in fetch_feed(engine)}

    assert feed["alice"].reply_status == "published"
    assert feed["alice"].reply_text == "pub reply"

    assert feed["bob"].reply_status == "error"
    assert feed["bob"].error_reason == "rate limit"

    assert feed["carol"].reply_status is None  # «нет ответа»
    assert feed["carol"].reply_text is None


def test_feed_picks_last_reply(engine):
    _add_comment(
        engine, cid=1, author="alice", text="hi", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="first", status="error",
               error_reason="boom")
    _add_reply(engine, rid=2, comment_id=1, generated_text="second", status="published")

    row = fetch_feed(engine)[0]
    assert row.reply_text == "second"
    assert row.reply_status == "published"


def test_feed_preserves_legacy_history_fallback_and_normalizes_post_url(engine):
    _add_comment(
        engine,
        cid=1,
        author="alice",
        text="current message",
        post_url="/a/legacy-post",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_comment(
        engine,
        cid=2,
        author="bob",
        text="another current message",
        post_url="https://dzen.ru/a/new-post",
        thread_text="alice: first message\\ncarol: second message",
        fetched_at=datetime(2026, 1, 1, 12, 1, 0),
    )

    feed = {row.author: row for row in fetch_feed(engine)}

    assert feed["alice"].thread_text is None
    assert feed["alice"].post_url == "https://dzen.ru/a/legacy-post"
    assert feed["bob"].thread_text == "alice: first message\\ncarol: second message"


@pytest.mark.parametrize(
    ("stored_url", "expected_url"),
    [
        ("/a/legacy-post", "https://dzen.ru/a/legacy-post"),
        ("https://dzen.ru/a/new-post", "https://dzen.ru/a/new-post"),
        ("https://www.dzen.ru/a/new-post", "https://www.dzen.ru/a/new-post"),
        ("javascript:alert(1)", None),
        ("data:text/html,unsafe", None),
        ("http://dzen.ru/a/insecure", None),
        ("https://evil.example/a/not-dzen", None),
        ("https://dzen.ru/not-a-post", None),
    ],
)
def test_feed_allows_only_safe_dzen_post_urls(engine, stored_url, expected_url):
    _add_comment(
        engine,
        cid=1,
        author="alice",
        text="comment",
        post_url=stored_url,
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )

    assert fetch_feed(engine)[0].post_url == expected_url


# --- route / rendering ---


def test_comments_page_renders_feed(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="great post", post_url="https://dzen.ru/a/xyz",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="thanks!", status="published")

    resp = client.get("/comments")
    assert resp.status_code == 200
    body = resp.text
    assert "alice" in body
    assert "great post" in body
    assert "thanks!" in body
    assert "Отправлен" in body
    assert 'href="https://dzen.ru/a/xyz"' in body
    assert 'target="_blank"' in body
    assert 'rel="noopener noreferrer"' in body


def test_comments_page_renders_title_link_above_dialogue_and_seconds_only(client, engine):
    _add_comment(
        engine,
        cid=1,
        author="alice",
        text="current",
        post_title="Заголовок публикации",
        post_url="/a/post-1",
        thread_text="first: earlier",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0, 123456),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="bot answer", status="published")

    body = client.get("/comments").text

    assert 'class="thread-post"' in body
    assert 'class="thread-tree"' in body
    assert 'href="https://dzen.ru/a/post-1"' in body
    assert body.index("Заголовок публикации") < body.index("earlier")
    assert body.index("earlier") < body.index("Комментарий пользователя")
    assert body.index("Комментарий пользователя") < body.index("Ответ бота")
    assert "2026-01-01 12:00:00" in body
    assert "2026-01-01 12:00:00.123456" not in body


def test_comments_page_keeps_generic_post_link_without_title(client, engine):
    _add_comment(
        engine,
        cid=1,
        author="alice",
        text="current",
        post_url="/a/post-1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )

    body = client.get("/comments").text

    assert "Заголовок публикации" not in body
    assert ">Открыть пост</a>" in body


@pytest.mark.parametrize("post_url", [None, ""])
def test_comments_page_shows_post_placeholder_without_safe_url(client, engine, post_url):
    _add_comment(
        engine,
        cid=1,
        author="alice",
        text="current",
        post_url=post_url,
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )

    body = client.get("/comments").text

    assert body.count("Ссылка отсутствует") == 2
    assert 'class="thread-current thread-depth-1"' in body


def test_comments_page_shows_no_reply_label(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="hmm", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    resp = client.get("/comments")
    assert "Нет ответа" in resp.text
    assert 'class="status-badge status-no-reply"' in resp.text


def test_comments_page_omits_unsafe_post_link(client, engine):
    _add_comment(
        engine,
        cid=1,
        author="alice",
        text="hmm",
        post_url="javascript:alert(1)",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )

    body = client.get("/comments").text

    assert "javascript:alert(1)" not in body
    assert "Открыть пост" not in body
    assert "Ссылка отсутствует" in body


def test_comments_page_shows_error_reason(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="hmm", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="x", status="error",
               error_reason="quota exceeded")
    resp = client.get("/comments")
    assert "quota exceeded" in resp.text


@pytest.mark.parametrize(
    ("status", "label", "badge_class"),
    [
        ("published", "Отправлен", "status-published"),
        ("generated", "Сгенерирован", "status-generated"),
        ("error", "Ошибка", "status-error"),
        ("skipped", "Пропущен", "status-skipped"),
    ],
)
def test_comments_page_renders_russian_status_badges(
    client, engine, status, label, badge_class
):
    _add_comment(
        engine, cid=1, author="alice", text="hmm", post_url="/a/post-1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="x", status=status)

    body = client.get("/comments").text

    assert label in body
    assert f'class="status-badge {badge_class}"' in body
    assert 'href="https://dzen.ru/a/post-1"' in body


def test_comments_page_renders_dialogue_and_legacy_fallback(client, engine):
    _add_comment(
        engine, cid=1, author="legacy", text="current", post_url="/a/legacy",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_comment(
        engine, cid=2, author="new", text="latest", post_url="/a/new",
        thread_text="first: hello\nsecond: world",
        fetched_at=datetime(2026, 1, 1, 12, 1, 0),
    )

    body = client.get("/comments").text

    assert "История до комментария не сохранена" in body
    assert "hello" in body
    assert "world" in body
    assert "Комментарий пользователя" in body


def test_comments_page_renders_thread_as_nested_nodes(client, engine):
    _add_comment(
        engine, cid=1, author="new", text="latest", post_url="/a/new",
        thread_text="alice: hello\nbob: hi there",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )

    body = client.get("/comments").text

    assert 'class="thread-node thread-depth-0"' in body
    assert 'class="thread-node thread-depth-1"' in body
    assert 'class="thread-current thread-depth-2"' in body
    assert body.index("hello") < body.index("hi there") < body.index("latest")
    assert 'class="thread-message"' not in body
    assert 'class="current-comment"' not in body
    assert 'class="bot-reply"' not in body
    assert "Лайк" not in body
    assert "Дизлайк" not in body
    assert "Ответить" not in body


def test_comments_page_bot_reply_block_present_and_distinct(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="hi", post_url="/a/p",
        thread_text="alice: hello",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="bot answer", status="published")

    body = client.get("/comments").text

    assert 'class="thread-bot-reply thread-depth-2"' in body
    assert "Ответ бота" in body
    assert "bot answer" in body
    assert 'class="thread-current thread-depth-1"' in body


def test_comments_page_renders_long_multiline_comment_and_reply(client, engine):
    long_text = "длинный комментарий " * 40 + "\nвторая строка"
    _add_comment(
        engine, cid=1, author="alice", text=long_text, post_url="/a/p",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text=long_text, status="published")

    body = client.get("/comments").text

    assert body.count(long_text) == 2
    assert body.count('class="thread-text"') == 2


def test_comments_page_no_bot_reply_when_empty(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="hi", post_url="/a/p",
        thread_text="alice: hello",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    # No reply added → no bot reply node.
    body = client.get("/comments").text

    assert 'class="thread-bot-reply"' not in body
    assert "Ответ бота" not in body


def test_comments_page_has_five_columns_and_no_reply_column(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="hi", post_url="/a/p",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )

    body = client.get("/comments").text

    assert "<th>Ответ бота</th>" not in body
    assert 'class="reply-cell"' not in body

    thead = body.split("<thead>", 1)[1].split("</thead>", 1)[0]
    headers = re.findall(r"<th>(.*?)</th>", thead)
    assert headers == ["Автор", "Диалог", "Статус", "Пост", "Время"]


def test_comments_page_fresh_first_in_html(client, engine):
    _add_comment(
        engine, cid=1, author="older", text="a", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_comment(
        engine, cid=2, author="newer", text="b", post_url="http://post/2",
        fetched_at=datetime(2026, 1, 1, 13, 0, 0),
    )
    body = client.get("/comments").text
    assert body.index("newer") < body.index("older")


def test_refresh_button_reissues_get_and_reflects_db(client, engine):
    body = client.get("/comments").text
    assert 'action="/comments"' in body
    assert 'method="get"' in body
    assert "Обновить" in body

    # New data added → a fresh GET reflects it.
    _add_comment(
        engine, cid=9, author="freshguy", text="new one", post_url="http://post/9",
        fetched_at=datetime(2026, 1, 1, 14, 0, 0),
    )
    assert "freshguy" in client.get("/comments").text


# --- fetch_feed filters ---


@pytest.fixture
def mixed_feed(engine):
    """Три автора: alice/error, Bob/no_reply, alina/published."""
    _add_comment(
        engine, cid=1, author="alice", text="a", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 3),
    )
    _add_comment(
        engine, cid=2, author="Bob", text="b", post_url="http://post/2",
        fetched_at=datetime(2026, 1, 1, 12, 0, 2),
    )
    _add_comment(
        engine, cid=3, author="alina", text="c", post_url="http://post/3",
        fetched_at=datetime(2026, 1, 1, 12, 0, 1),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="x", status="error",
               error_reason="boom")
    _add_reply(engine, rid=3, comment_id=3, generated_text="y", status="published")
    # comment 2 (Bob) has no reply
    return engine


def test_fetch_feed_status_error_filter(mixed_feed):
    rows = fetch_feed(mixed_feed, status="error")
    assert [r.author for r in rows] == ["alice"]
    assert all(r.reply_status == "error" for r in rows)


def test_fetch_feed_status_no_reply_filter(mixed_feed):
    rows = fetch_feed(mixed_feed, status="no_reply")
    assert [r.author for r in rows] == ["Bob"]
    assert all(r.reply_status is None for r in rows)


def test_fetch_feed_no_status_unchanged(mixed_feed):
    assert len(fetch_feed(mixed_feed)) == 3


def test_fetch_feed_author_query_case_insensitive(mixed_feed):
    rows = fetch_feed(mixed_feed, author_query="ali")
    assert sorted(r.author for r in rows) == ["alice", "alina"]


def test_fetch_feed_author_query_empty_no_filter(mixed_feed):
    assert len(fetch_feed(mixed_feed, author_query="")) == 3
    assert len(fetch_feed(mixed_feed, author_query=None)) == 3


# --- fetch_status_counts ---


def test_fetch_status_counts(engine):
    specs = [
        (1, "error"), (2, "error"),
        (3, "generated"),
        (4, "published"),
        (5, "skipped"),
        (6, None),  # no reply
        (7, None),
    ]
    for cid, status in specs:
        _add_comment(
            engine, cid=cid, author=f"a{cid}", text="t", post_url=f"http://post/{cid}",
            fetched_at=datetime(2026, 1, 1, 12, 0, cid),
        )
        if status is not None:
            _add_reply(engine, rid=cid, comment_id=cid, generated_text="r", status=status)

    counts = fetch_status_counts(engine)
    assert counts["error"] == 2
    assert counts["generated"] == 1
    assert counts["published"] == 1
    assert counts["skipped"] == 1
    assert counts["no_reply"] == 2
    assert sum(counts.values()) == 7


# --- route filters & rendering ---


def _tbody_row_count(body: str) -> int:
    tbody = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return tbody.count("<tr")


def test_comments_status_filter_renders_only_error(client, mixed_feed):
    body = client.get("/comments?status=error").text
    assert "alice" in body
    assert "alina" not in body
    assert "Bob" not in body
    assert '<option value="error" selected>Ошибка</option>' in body


def test_comments_author_query_renders_and_preserves_input(client, mixed_feed):
    body = client.get("/comments?q=alice").text
    assert "alice" in body
    assert "alina" not in body
    assert 'name="q" value="alice"' in body


def test_comments_author_datalist_contains_each_nonempty_displayed_author_once(client, engine):
    for cid, author in enumerate(("alice", "Alice", "alice", ""), start=1):
        _add_comment(
            engine,
            cid=cid,
            author=author,
            text="comment",
            post_url="/a/post",
            fetched_at=datetime(2026, 1, 1, 12, 0, cid),
        )

    body = client.get("/comments?q=ali").text

    assert '<input type="text" name="q" list="author-options" value="ali">' in body
    datalist = body.split('<datalist id="author-options">', 1)[1].split("</datalist>", 1)[0]
    assert datalist.count('<option value="alice">') == 1
    assert datalist.count('<option value="Alice">') == 1
    assert '<option value="">' not in datalist


def test_comments_compact_thread_styles_center_every_table_header():
    css = (Path(__file__).parents[2] / "dzen_commenter/admin/static/style.css").read_text(
        encoding="utf-8"
    )

    assert "table.feed thead th { text-align: center;" in css
    assert "min-width: 760px" in css
    assert "table.feed th:nth-child(2) { width: 42%; }" in css
    assert ".dialogue-cell { white-space: normal; }" in css
    assert ".thread-text { white-space: pre-wrap; overflow-wrap: anywhere; }" in css
    assert ".post-link-placeholder { color: var(--muted); }" in css
    assert "text-indent" not in css
    assert ".thread-tree { display: grid; gap: 4px; border-left: 1px solid var(--line); }" in css
    assert ".thread-depth-1 { margin-left: 16px; }" in css
    assert ".thread-depth-2 { margin-left: 32px; }" in css
    assert ".thread-depth-3 { margin-left: 48px; }" in css
    assert ".thread-node, .thread-current, .thread-bot-reply" in css
    assert ".thread-message" not in css
    assert ".current-comment" not in css
    assert ".bot-reply" not in css


def test_comments_count_matches_rendered_rows(client, mixed_feed):
    body = client.get("/comments?q=ali").text
    assert _tbody_row_count(body) == 2
    assert "Показано 2 из последних 100" in body


def test_comments_error_rows_get_attention_class(client, mixed_feed):
    body = client.get("/comments").text
    assert 'class="row-attention"' in body


# --- home ---


def test_home_renders_counts_and_links(client, mixed_feed):
    body = client.get("/").text
    assert "Ошибок" in body
    assert "Без ответа" in body
    assert 'href="/comments"' in body
    assert 'href="/settings"' in body


def test_guest_redirected_to_login(engine):
    settings = AdminSettings(
        _env_file=None,
        ADMIN_PASSWORD=PASSWORD,
        ADMIN_SESSION_SECRET="test-session-secret",
    )
    app = create_app(settings, engine=engine)
    guest = TestClient(app, follow_redirects=False)
    resp = guest.get("/comments")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"
