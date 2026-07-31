from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect, text

from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.interfaces import CommentRepository
from dzen_commenter.contracts.models import Comment, Publication, Reply
from dzen_commenter.db.repository import PostgresCommentRepository


@pytest.fixture
def repo(engine):
    return PostgresCommentRepository(engine)


def _make_publication(dzen_id="pub-1", title="T", url="http://x") -> Publication:
    return Publication(id=None, dzen_publication_id=dzen_id, title=title, url=url)


def _make_comment(
    publication_id,
    dzen_id="c-1",
    text="hello",
    status=CommentStatus.NEW,
    post_url="http://post/1",
    publication_title="",
    thread_text="",
) -> Comment:
    return Comment(
        id=None,
        dzen_comment_id=dzen_id,
        publication_id=publication_id,
        author="alice",
        text=text,
        parent_comment_id=None,
        posted_at=datetime(2026, 1, 1, 12, 0, 0),
        fetched_at=datetime(2026, 1, 1, 12, 5, 0),
        status=status,
        publication_title=publication_title,
        post_url=post_url,
        thread_text=thread_text,
    )


def _make_reply(
    comment_id,
    status=ReplyStatus.GENERATED,
    *,
    published_at=None,
    is_cta_candidate=False,
) -> Reply:
    return Reply(
        id=None,
        comment_id=comment_id,
        generated_text="reply text",
        ai_provider="openai",
        ai_model="gpt-4o-mini",
        status=status,
        published_at=published_at,
        error_reason=None,
        created_at=datetime(2026, 1, 1, 12, 10, 0),
        article_context_status="article_text_used",
        is_cta_candidate=is_cta_candidate,
    )


# --- Acceptance 1: migrations applied, tables exist with expected columns ---


def test_tables_exist_with_columns(engine):
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert {"publications", "comments", "replies"} <= tables

    pub_cols = {c["name"] for c in insp.get_columns("publications")}
    assert {"id", "dzen_publication_id", "title", "url"} <= pub_cols

    com_cols = {c["name"] for c in insp.get_columns("comments")}
    assert {
        "id",
        "dzen_comment_id",
        "publication_id",
        "author",
        "text",
        "parent_comment_id",
        "posted_at",
        "fetched_at",
        "status",
        "post_title",
        "thread_text",
    } <= com_cols

    rep_cols = {c["name"] for c in insp.get_columns("replies")}
    assert {
        "id",
        "comment_id",
        "generated_text",
        "ai_provider",
        "ai_model",
        "status",
        "published_at",
        "error_reason",
        "created_at",
        "article_context_status",
        "is_cta_candidate",
    } <= rep_cols


def test_save_reply_stores_article_context_status(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    comment_id = repo.upsert_comment(_make_comment(publication_id))

    reply_id = repo.save_reply(_make_reply(comment_id))

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT article_context_status FROM replies WHERE id = :id"),
            {"id": reply_id},
        ).scalar_one()
    assert stored == "article_text_used"


def test_published_reply_counters_ignore_errors_and_old_replies(repo):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 7, 31, 12, 0, 0)
    recent_candidate = repo.upsert_comment(_make_comment(publication_id, dzen_id="recent"))
    old_candidate = repo.upsert_comment(_make_comment(publication_id, dzen_id="old"))
    failed_candidate = repo.upsert_comment(_make_comment(publication_id, dzen_id="failed"))

    repo.save_reply(
        _make_reply(
            recent_candidate,
            status=ReplyStatus.PUBLISHED,
            published_at=now,
            is_cta_candidate=True,
        )
    )
    repo.save_reply(
        _make_reply(
            old_candidate,
            status=ReplyStatus.PUBLISHED,
            published_at=now - timedelta(hours=1, microseconds=1),
            is_cta_candidate=True,
        )
    )
    repo.save_reply(
        _make_reply(
            failed_candidate,
            status=ReplyStatus.ERROR,
            published_at=now,
            is_cta_candidate=True,
        )
    )

    assert repo.count_published_replies_since(now - timedelta(hours=1)) == 1
    assert repo.count_published_cta_candidates() == 2


# --- Acceptance 2: UNIQUE constraints ---


def test_unique_constraints(engine):
    insp = inspect(engine)

    pub_unique_cols = {
        tuple(uc["column_names"]) for uc in insp.get_unique_constraints("publications")
    }
    assert ("dzen_publication_id",) in pub_unique_cols

    com_unique_cols = {
        tuple(uc["column_names"]) for uc in insp.get_unique_constraints("comments")
    }
    assert ("dzen_comment_id",) in com_unique_cols


# --- Acceptance 3: FK constraints ---


def test_foreign_keys(engine):
    insp = inspect(engine)

    com_fks = insp.get_foreign_keys("comments")
    assert any(
        fk["referred_table"] == "publications"
        and fk["constrained_columns"] == ["publication_id"]
        and fk["referred_columns"] == ["id"]
        for fk in com_fks
    )

    rep_fks = insp.get_foreign_keys("replies")
    assert any(
        fk["referred_table"] == "comments"
        and fk["constrained_columns"] == ["comment_id"]
        and fk["referred_columns"] == ["id"]
        for fk in rep_fks
    )


# --- Acceptance 4: repository fulfils the contract ---


def test_repository_fulfils_contract(repo):
    for method in (
        "upsert_publication",
        "upsert_comment",
        "save_reply",
        "set_comment_status",
        "set_reply_status",
        "has_generated_reply",
        "has_published_reply",
        "is_own_reply",
        "count_published_replies_since",
        "count_published_cta_candidates",
    ):
        assert callable(getattr(repo, method))

    # isinstance only works if the frozen Protocol is @runtime_checkable.
    if getattr(CommentRepository, "_is_runtime_protocol", False):
        assert isinstance(repo, CommentRepository)


# --- Acceptance 6: upsert_publication idempotent ---


def test_upsert_publication_idempotent(repo, engine):
    pub = _make_publication()
    id1 = repo.upsert_publication(pub)
    id2 = repo.upsert_publication(_make_publication())
    assert id1 == id2

    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM publications")).scalar_one()
    assert count == 1


# --- Acceptance 5: upsert_comment idempotent ---


def test_upsert_comment_idempotent(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    c1 = repo.upsert_comment(_make_comment(pub_id))
    c2 = repo.upsert_comment(_make_comment(pub_id))
    assert c1 == c2

    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM comments")).scalar_one()
    assert count == 1


# --- Acceptance 7: upsert updates, not duplicates ---


def test_upsert_comment_updates(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id, text="old", status=CommentStatus.NEW))
    repo.upsert_comment(
        _make_comment(pub_id, text="new", status=CommentStatus.ANSWERED)
    )

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT text, status FROM comments WHERE id = :id"), {"id": cid}
        ).one()
        count = conn.execute(text("SELECT COUNT(*) FROM comments")).scalar_one()
    assert row.text == "new"
    assert row.status == CommentStatus.ANSWERED.value
    assert count == 1


# --- Acceptance 09: upsert stores and updates post_url ---


def test_upsert_comment_stores_and_updates_post_url(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id, post_url="http://post/old"))

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT post_url FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one()
    assert stored == "http://post/old"

    repo.upsert_comment(_make_comment(pub_id, post_url="http://post/new"))

    with engine.begin() as conn:
        updated = conn.execute(
            text("SELECT post_url FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one()
    assert updated == "http://post/new"


def test_upsert_comment_keeps_prior_post_url_when_rescrape_fails_to_capture_it(
    repo, engine
):
    """Ре-скрейп уже известного комментария с несработавшим захватом ссылки
    (post_url=None) не должен затирать ранее сохранённую ссылку — иначе
    ссылка «пропадает» из админки для уже отвеченных комментариев."""
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id, post_url="http://post/known"))

    repo.upsert_comment(_make_comment(pub_id, post_url=None))

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT post_url FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one()
    assert stored == "http://post/known"


def test_upsert_comment_stores_and_updates_post_title(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(
        _make_comment(pub_id, publication_title="Первый заголовок")
    )

    with engine.begin() as conn:
        assert conn.execute(
            text("SELECT post_title FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one() == "Первый заголовок"

    repo.upsert_comment(_make_comment(pub_id, publication_title="Новый заголовок"))
    with engine.begin() as conn:
        assert conn.execute(
            text("SELECT post_title FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one() == "Новый заголовок"


def test_upsert_comment_keeps_prior_post_title_when_rescrape_fails_to_capture_it(
    repo, engine
):
    """Ре-скрейп с несработавшим захватом заголовка (publication_title="") не должен
    затирать ранее сохранённый заголовок — иначе в столбце «Диалог» вместо названия
    поста внезапно появляется запасное «Открыть пост»."""
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(
        _make_comment(pub_id, publication_title="Известный заголовок")
    )

    repo.upsert_comment(_make_comment(pub_id, publication_title=""))

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT post_title FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one()
    assert stored == "Известный заголовок"


def test_upsert_comment_stores_thread_text_and_keeps_legacy_null(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    comment_id = repo.upsert_comment(
        _make_comment(pub_id, thread_text="alice: hello\\nbob: reply")
    )

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT thread_text FROM comments WHERE id = :id"), {"id": comment_id}
        ).scalar_one()

    assert stored == "alice: hello\\nbob: reply"


def test_upsert_preserves_legacy_null_until_a_real_history_arrives(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO comments "
                "(dzen_comment_id, publication_id, status, thread_text) "
                "VALUES (:dzen_comment_id, :publication_id, 'new', NULL)"
            ),
            {"dzen_comment_id": "legacy", "publication_id": pub_id},
        )

    repo.upsert_comment(_make_comment(pub_id, dzen_id="legacy", thread_text=""))
    with engine.begin() as conn:
        assert conn.execute(
            text("SELECT thread_text FROM comments WHERE dzen_comment_id = 'legacy'")
        ).scalar_one() is None

    repo.upsert_comment(
        _make_comment(pub_id, dzen_id="legacy", thread_text="alice: prior message")
    )
    with engine.begin() as conn:
        assert conn.execute(
            text("SELECT thread_text FROM comments WHERE dzen_comment_id = 'legacy'")
        ).scalar_one() == "alice: prior message"


# --- Acceptance 8: status transitions ---


def test_set_comment_status(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id))
    repo.set_comment_status(cid, CommentStatus.SKIPPED)

    with engine.begin() as conn:
        status = conn.execute(
            text("SELECT status FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one()
    assert status == CommentStatus.SKIPPED.value


def test_set_reply_status_with_error(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id))
    rid = repo.save_reply(_make_reply(cid))
    repo.set_reply_status(rid, ReplyStatus.ERROR, error_reason="boom")

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, error_reason FROM replies WHERE id = :id"),
            {"id": rid},
        ).one()
    assert row.status == ReplyStatus.ERROR.value
    assert row.error_reason == "boom"


# --- Acceptance 9: has_published_reply ---


def test_has_published_reply(repo):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id))

    assert repo.has_published_reply(cid) is False

    rid = repo.save_reply(_make_reply(cid, status=ReplyStatus.GENERATED))
    assert repo.has_published_reply(cid) is False

    repo.set_reply_status(rid, ReplyStatus.PUBLISHED)
    assert repo.has_published_reply(cid) is True


def test_has_published_reply_ignores_non_published(repo):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id))
    rid = repo.save_reply(_make_reply(cid, status=ReplyStatus.GENERATED))
    repo.set_reply_status(rid, ReplyStatus.ERROR, error_reason="x")
    assert repo.has_published_reply(cid) is False


def test_has_generated_reply_includes_generated_and_published(repo):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id))

    assert repo.has_generated_reply(cid) is False

    rid = repo.save_reply(_make_reply(cid, status=ReplyStatus.GENERATED))
    assert repo.has_generated_reply(cid) is True

    repo.set_reply_status(rid, ReplyStatus.PUBLISHED)
    assert repo.has_generated_reply(cid) is True


def test_has_generated_reply_ignores_errors(repo):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id))
    rid = repo.save_reply(_make_reply(cid, status=ReplyStatus.GENERATED))

    repo.set_reply_status(rid, ReplyStatus.ERROR, error_reason="x")

    assert repo.has_generated_reply(cid) is False


# --- Acceptance 10: is_own_reply detects our own reply re-scraped as a comment ---


def test_is_own_reply_matches_published_reply_text_under_same_post(repo):
    pub_id = repo.upsert_publication(_make_publication())
    parent_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="parent-1", post_url="http://post/1")
    )
    rid = repo.save_reply(_make_reply(parent_id, status=ReplyStatus.GENERATED))

    assert repo.is_own_reply("http://post/1", "reply text") is False

    repo.set_reply_status(rid, ReplyStatus.PUBLISHED)

    assert repo.is_own_reply("http://post/1", "reply text") is True


def test_is_own_reply_ignores_unrelated_text_or_post(repo):
    pub_id = repo.upsert_publication(_make_publication())
    parent_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="parent-1", post_url="http://post/1")
    )
    repo.save_reply(_make_reply(parent_id, status=ReplyStatus.PUBLISHED))

    assert repo.is_own_reply("http://post/1", "someone else's comment") is False
    assert repo.is_own_reply("http://post/2", "reply text") is False
    assert repo.is_own_reply(None, "reply text") is False
