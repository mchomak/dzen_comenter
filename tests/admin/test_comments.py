from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from dzen_commenter.admin.app import create_app
from dzen_commenter.admin.config import AdminSettings
from dzen_commenter.admin.queries import fetch_feed
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


def _add_comment(engine, *, cid, author, text, post_url, fetched_at):
    with engine.begin() as conn:
        conn.execute(
            insert(CommentTable).values(
                id=cid,
                dzen_comment_id=f"c-{cid}",
                publication_id=1,
                author=author,
                text=text,
                post_url=post_url,
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


# --- route / rendering ---


def test_comments_page_renders_feed(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="great post", post_url="http://post/xyz",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="thanks!", status="published")

    resp = client.get("/comments")
    assert resp.status_code == 200
    body = resp.text
    assert "alice" in body
    assert "great post" in body
    assert "thanks!" in body
    assert "published" in body
    assert 'href="http://post/xyz"' in body


def test_comments_page_shows_no_reply_label(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="hmm", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    resp = client.get("/comments")
    assert "нет ответа" in resp.text


def test_comments_page_shows_error_reason(client, engine):
    _add_comment(
        engine, cid=1, author="alice", text="hmm", post_url="http://post/1",
        fetched_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    _add_reply(engine, rid=1, comment_id=1, generated_text="x", status="error",
               error_reason="quota exceeded")
    resp = client.get("/comments")
    assert "quota exceeded" in resp.text


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
