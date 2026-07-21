from datetime import datetime

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
        post_url=post_url,
    )


def _make_reply(comment_id, status=ReplyStatus.GENERATED) -> Reply:
    return Reply(
        id=None,
        comment_id=comment_id,
        generated_text="reply text",
        ai_provider="openai",
        ai_model="gpt-4o-mini",
        status=status,
        published_at=None,
        error_reason=None,
        created_at=datetime(2026, 1, 1, 12, 10, 0),
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
    } <= rep_cols


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
