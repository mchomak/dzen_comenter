from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

import dzen_commenter.db.repository as repository_module
from dzen_commenter.contracts.enums import (
    CommentStatus,
    GenerationFailureOutcome,
    ReplyStatus,
)
from dzen_commenter.contracts.interfaces import CommentRepository
from dzen_commenter.contracts.models import Comment, Publication, Reply
from dzen_commenter.db.models import (
    CommentTable,
    ReplyGenerationQueueTable,
    ReplyPublicationQueueTable,
    ReplyTable,
)
from dzen_commenter.db.repository import PostgresCommentRepository


REPO_ROOT = Path(__file__).resolve().parents[2]


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
    fetched_at=datetime(2026, 1, 1, 12, 5, 0),
) -> Comment:
    return Comment(
        id=None,
        dzen_comment_id=dzen_id,
        publication_id=publication_id,
        author="alice",
        text=text,
        parent_comment_id=None,
        posted_at=datetime(2026, 1, 1, 12, 0, 0),
        fetched_at=fetched_at,
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
    assert {"publications", "comments", "replies", "reply_publication_queue"} <= tables

    pub_cols = {c["name"] for c in insp.get_columns("publications")}
    assert {
        "id",
        "dzen_publication_id",
        "title",
        "url",
        "article_text",
        "article_fetched_at",
        "article_content_hash",
        "article_context_status",
    } <= pub_cols

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


def test_generation_queue_migration_preserves_batch_tables(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert {
        "comment_batch_queue",
        "reply_batch_items",
        "reply_batches",
        "reply_generation_queue",
    } <= tables
    assert {
        "comment_id",
        "state",
        "attempt_count",
        "next_attempt_at",
        "last_error",
        "created_at",
        "claimed_at",
        "claim_token",
    } <= {column["name"] for column in inspector.get_columns("reply_generation_queue")}
    assert inspector.get_pk_constraint("reply_generation_queue")["constrained_columns"] == [
        "comment_id"
    ]
    assert any(
        index["name"] == "ix_reply_generation_queue_ready"
        for index in inspector.get_indexes("reply_generation_queue")
    )
    assert "claim_token" in {
        column["name"] for column in inspector.get_columns("reply_publication_queue")
    }


def test_generation_queue_migration_preserves_seeded_batch_era_rows(engine):
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(REPO_ROOT / "dzen_commenter" / "db" / "migrations")
    )
    command.downgrade(config, "0009_article_publication_queue")
    queued_at = datetime(2026, 9, 18, 12, 0, 0)
    with engine.begin() as conn:
        publication_id = conn.execute(
            text(
                "INSERT INTO publications (dzen_publication_id, title, url) "
                "VALUES ('batch-era-publication', 'Batch title', 'http://batch') "
                "RETURNING id"
            )
        ).scalar_one()
        comment_id = conn.execute(
            text(
                "INSERT INTO comments (dzen_comment_id, publication_id, author, text, "
                "status, post_url) VALUES "
                "('batch-era-comment', :publication_id, 'alice', 'question', "
                "'generating', 'http://batch') RETURNING id"
            ),
            {"publication_id": publication_id},
        ).scalar_one()
        reply_id = conn.execute(
            text(
                "INSERT INTO replies (comment_id, generated_text, status, created_at, "
                "article_context_status, is_cta_candidate) VALUES "
                "(:comment_id, 'answer', 'generated', :queued_at, "
                "'article_text_used', true) RETURNING id"
            ),
            {"comment_id": comment_id, "queued_at": queued_at},
        ).scalar_one()
        batch_id = conn.execute(
            text(
                "INSERT INTO reply_batches (post_url, created_at, status, item_count, "
                "article_context_status, prompt_tokens, completion_tokens, error_reason) "
                "VALUES ('http://batch', :queued_at, 'processing', 1, "
                "'article_text_used', 21, 34, NULL) RETURNING id"
            ),
            {"queued_at": queued_at},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO comment_batch_queue "
                "(comment_id, post_url, queued_at, state, attempt_count, next_attempt_at, "
                "claimed_batch_id) VALUES "
                "(:comment_id, 'http://batch', :queued_at, 'claimed', 2, NULL, :batch_id)"
            ),
            {"comment_id": comment_id, "queued_at": queued_at, "batch_id": batch_id},
        )
        conn.execute(
            text(
                "INSERT INTO reply_batch_items "
                "(batch_id, comment_id, item_no, status, reply_id) VALUES "
                "(:batch_id, :comment_id, 1, 'generated', :reply_id)"
            ),
            {"batch_id": batch_id, "comment_id": comment_id, "reply_id": reply_id},
        )

    command.upgrade(config, "head")

    with engine.connect() as conn:
        batch = conn.execute(
            text(
                "SELECT post_url, created_at, status, item_count, article_context_status, "
                "prompt_tokens, completion_tokens, error_reason FROM reply_batches"
            )
        ).one()
        queue = conn.execute(
            text(
                "SELECT comment_id, post_url, queued_at, state, attempt_count, "
                "next_attempt_at, claimed_batch_id FROM comment_batch_queue"
            )
        ).one()
        item = conn.execute(
            text(
                "SELECT batch_id, comment_id, item_no, status, reply_id "
                "FROM reply_batch_items"
            )
        ).one()
    assert batch == (
        "http://batch",
        queued_at,
        "processing",
        1,
        "article_text_used",
        21,
        34,
        None,
    )
    assert queue == (comment_id, "http://batch", queued_at, "claimed", 2, None, batch_id)
    assert item == (batch_id, comment_id, 1, "generated", reply_id)


def test_article_context_migration_preserves_existing_publications(engine):
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(REPO_ROOT / "dzen_commenter" / "db" / "migrations")
    )
    command.downgrade(config, "0008_reply_batches")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO publications (dzen_publication_id, title, url) "
                "VALUES ('before-0009', 'Existing title', 'http://existing')"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT title, url, article_text, article_fetched_at, "
                "article_content_hash, article_context_status "
                "FROM publications WHERE dzen_publication_id = 'before-0009'"
            )
        ).one()
    assert row == ("Existing title", "http://existing", None, None, None, None)


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
    draft_candidate = repo.upsert_comment(_make_comment(publication_id, dzen_id="draft"))
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
            draft_candidate,
            status=ReplyStatus.GENERATED,
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
    assert repo.count_cta_candidates_produced() == 3


def test_ai_attempt_counter_includes_recent_generated_published_and_errors(repo):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 7, 31, 12, 0, 0)
    recent_statuses = (
        ReplyStatus.GENERATED,
        ReplyStatus.PUBLISHED,
        ReplyStatus.ERROR,
    )

    for index, status in enumerate(recent_statuses):
        comment_id = repo.upsert_comment(
            _make_comment(publication_id, dzen_id=f"recent-{index}")
        )
        reply = _make_reply(comment_id, status=status)
        reply.created_at = now
        repo.save_reply(reply)

    old_comment_id = repo.upsert_comment(_make_comment(publication_id, dzen_id="old"))
    old_reply = _make_reply(old_comment_id)
    old_reply.created_at = now - timedelta(hours=1, microseconds=1)
    repo.save_reply(old_reply)

    skipped_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="skipped")
    )
    skipped_reply = _make_reply(skipped_comment_id, status=ReplyStatus.SKIPPED)
    skipped_reply.created_at = now
    repo.save_reply(skipped_reply)

    assert repo.count_ai_attempts_since(now - timedelta(hours=1)) == 4


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
        "upsert_eligible_comment",
        "enqueue_pending_generations",
        "save_reply",
        "set_comment_status",
        "skip_comment_if_new",
        "set_reply_status",
        "has_generated_reply",
        "has_published_reply",
        "is_own_reply",
        "count_published_replies_since",
        "count_ai_attempts_since",
        "count_cta_candidates_produced",
        "enqueue_generation",
        "claim_next_generation",
        "complete_generation",
        "skip_generation",
        "fail_generation",
        "get_article_context",
        "save_article_context",
        "enqueue_publication",
        "expire_stale_publications",
        "claim_next_publication",
        "complete_publication",
        "fail_publication",
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


def test_upsert_eligible_comment_rolls_back_comment_when_queue_insert_fails(
    repo, engine
):
    publication_id = repo.upsert_publication(_make_publication())
    comment = _make_comment(publication_id, dzen_id="atomic-generation")

    with pytest.raises(IntegrityError):
        repo.upsert_eligible_comment(comment, queued_at=None)

    with engine.connect() as conn:
        comment_count = conn.execute(
            text("SELECT COUNT(*) FROM comments WHERE dzen_comment_id = 'atomic-generation'")
        ).scalar_one()
    assert comment_count == 0


# --- Acceptance 7: upsert updates, not duplicates ---


def test_upsert_comment_updates(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    cid = repo.upsert_comment(_make_comment(pub_id, text="old", status=CommentStatus.NEW))
    repo.upsert_comment(
        _make_comment(pub_id, text="new", status=CommentStatus.GENERATED)
    )

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT text, status FROM comments WHERE id = :id"), {"id": cid}
        ).one()
        count = conn.execute(text("SELECT COUNT(*) FROM comments")).scalar_one()
    assert row.text == "new"
    assert row.status == CommentStatus.GENERATED.value
    assert count == 1


def test_upsert_comment_keeps_first_seen_fetched_at_on_rescrape(repo, engine):
    """fetched_at фиксируется на моменте первого скрейпа и не обновляется на
    повторных скрейпах — иначе уже отвеченные комментарии в админке выглядят
    так, будто это происходит прямо сейчас."""
    pub_id = repo.upsert_publication(_make_publication())
    first_seen = datetime(2026, 1, 1, 12, 0, 0)
    cid = repo.upsert_comment(_make_comment(pub_id, fetched_at=first_seen))

    repo.upsert_comment(
        _make_comment(pub_id, fetched_at=datetime(2026, 1, 1, 15, 0, 0))
    )

    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT fetched_at FROM comments WHERE id = :id"), {"id": cid}
        ).scalar_one()
    assert stored == first_seen


def test_upsert_comment_preserves_queue_derived_status_on_rescrape(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    comment_id = repo.upsert_comment(_make_comment(publication_id))
    now = datetime(2026, 9, 18, 12, 0, 0)
    assert repo.enqueue_generation(comment_id, queued_at=now)
    assert repo.claim_next_generation(now) is not None

    repo.upsert_comment(
        _make_comment(
            publication_id,
            text="updated from rescrape",
            status=CommentStatus.NEW,
        )
    )

    with engine.connect() as conn:
        row = conn.execute(
            select(CommentTable.text, CommentTable.status).where(
                CommentTable.id == comment_id
            )
        ).one()
    assert row == ("updated from rescrape", CommentStatus.GENERATING.value)


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


def test_skip_comment_if_new_cannot_overwrite_a_queue_derived_status(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    new_comment_id = repo.upsert_comment(_make_comment(publication_id, dzen_id="new"))
    active_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="active")
    )
    now = datetime(2026, 9, 18, 12, 0, 0)
    assert repo.enqueue_generation(active_comment_id, queued_at=now)
    assert repo.claim_next_generation(now) is not None

    assert repo.skip_comment_if_new(new_comment_id)
    assert not repo.skip_comment_if_new(active_comment_id)

    with engine.connect() as conn:
        statuses = conn.execute(
            select(CommentTable.dzen_comment_id, CommentTable.status).order_by(
                CommentTable.dzen_comment_id
            )
        ).all()
    assert statuses == [("active", "generating"), ("new", "skipped")]


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


def test_generation_queue_enqueues_once_and_claims_the_earliest_ready_comment(
    repo, engine
):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    first_id = repo.upsert_comment(_make_comment(publication_id, dzen_id="first"))
    second_id = repo.upsert_comment(_make_comment(publication_id, dzen_id="second"))

    assert repo.enqueue_generation(second_id, queued_at=now)
    assert repo.enqueue_generation(first_id, queued_at=now - timedelta(seconds=1))
    assert not repo.enqueue_generation(first_id, queued_at=now)

    claimed = repo.claim_next_generation(now)

    assert claimed is not None
    assert claimed.comment.id == first_id
    assert claimed.attempt_count == 1
    assert claimed.claim_token
    with engine.connect() as conn:
        first_queue = conn.execute(
            select(
                ReplyGenerationQueueTable.state,
                ReplyGenerationQueueTable.attempt_count,
                ReplyGenerationQueueTable.claimed_at,
            ).where(ReplyGenerationQueueTable.comment_id == first_id)
        ).one()
        first_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == first_id)
        ).scalar_one()
    assert first_queue == ("claimed", 1, now)
    assert first_status == "generating"
    assert repo.claim_next_generation(now).comment.id == second_id


def test_enqueue_pending_generations_recovers_saved_new_comments_without_terminal_reply(
    repo, engine
):
    publication_id = repo.upsert_publication(_make_publication())
    recover_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="saved-before-deploy")
    )
    terminal_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="already-skipped")
    )
    repo.save_reply(_make_reply(terminal_id, status=ReplyStatus.SKIPPED))
    now = datetime(2026, 9, 18, 12, 0, 0)

    recovered = repo.enqueue_pending_generations(queued_at=now)

    assert recovered == 1
    with engine.connect() as conn:
        queue_comment_ids = conn.execute(
            select(ReplyGenerationQueueTable.comment_id).order_by(
                ReplyGenerationQueueTable.comment_id
            )
        ).scalars().all()
    assert queue_comment_ids == [recover_id]
    assert repo.enqueue_pending_generations(queued_at=now) == 0


def test_complete_generation_saves_reply_and_publication_job_atomically(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    comment_id = repo.upsert_comment(_make_comment(publication_id))
    now = datetime(2026, 9, 18, 12, 0, 0)
    assert repo.enqueue_generation(comment_id, queued_at=now)
    claim = repo.claim_next_generation(now)
    assert claim is not None

    reply_id = repo.complete_generation(
        comment_id,
        claim_token=claim.claim_token,
        text="Готовый ответ",
        ai_provider="test",
        ai_model="test-model",
        article_context_status="article_text_used",
        created_at=now,
        is_cta_candidate=True,
    )

    with engine.connect() as conn:
        reply = conn.execute(
            select(
                ReplyTable.generated_text,
                ReplyTable.status,
                ReplyTable.article_context_status,
                ReplyTable.is_cta_candidate,
            ).where(ReplyTable.id == reply_id)
        ).one()
        publication_state = conn.execute(
            select(ReplyPublicationQueueTable.state).where(
                ReplyPublicationQueueTable.reply_id == reply_id
            )
        ).scalar_one()
        generation_state = conn.execute(
            select(ReplyGenerationQueueTable.state).where(
                ReplyGenerationQueueTable.comment_id == comment_id
            )
        ).scalar_one()
        comment_state = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
    assert reply == ("Готовый ответ", "generated", "article_text_used", True)
    assert publication_state == "queued"
    assert generation_state == "completed"
    assert comment_state == "generated"


def test_skip_generation_is_terminal_without_a_publication_job(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    comment_id = repo.upsert_comment(_make_comment(publication_id))
    now = datetime(2026, 9, 18, 12, 0, 0)
    assert repo.enqueue_generation(comment_id, queued_at=now)
    claim = repo.claim_next_generation(now)
    assert claim is not None

    reply_id = repo.skip_generation(
        comment_id,
        claim_token=claim.claim_token,
        reason="Не требует ответа",
        ai_provider="test",
        ai_model="test-model",
        article_context_status="without_article_text",
        created_at=now,
    )

    with engine.connect() as conn:
        reply = conn.execute(
            select(ReplyTable.status, ReplyTable.error_reason).where(
                ReplyTable.id == reply_id
            )
        ).one()
        publication_count = conn.execute(
            select(ReplyPublicationQueueTable.reply_id).where(
                ReplyPublicationQueueTable.reply_id == reply_id
            )
        ).scalar_one_or_none()
        generation_state = conn.execute(
            select(ReplyGenerationQueueTable.state).where(
                ReplyGenerationQueueTable.comment_id == comment_id
            )
        ).scalar_one()
        comment_state = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
    assert reply == ("skipped", "Не требует ответа")
    assert publication_count is None
    assert generation_state == "completed"
    assert comment_state == "skipped"


def test_generation_failure_retries_then_becomes_a_terminal_generation_error(
    repo, engine
):
    publication_id = repo.upsert_publication(_make_publication())
    comment_id = repo.upsert_comment(_make_comment(publication_id))
    now = datetime(2026, 9, 18, 12, 0, 0)
    assert repo.enqueue_generation(comment_id, queued_at=now)
    claim = repo.claim_next_generation(now)
    assert claim is not None

    retry_outcome = repo.fail_generation(
        comment_id,
        claim_token=claim.claim_token,
        error_reason="AI unavailable",
        failed_at=now,
        ai_provider="test",
        ai_model="test-model",
        article_context_status="without_article_text",
        retry_cooldown_minutes=60,
        max_attempts_per_comment=2,
    )

    assert retry_outcome is GenerationFailureOutcome.RETRY
    with engine.connect() as conn:
        retry_queue = conn.execute(
            select(
                ReplyGenerationQueueTable.state,
                ReplyGenerationQueueTable.next_attempt_at,
                ReplyGenerationQueueTable.last_error,
            ).where(ReplyGenerationQueueTable.comment_id == comment_id)
        ).one()
        retry_comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
    assert retry_queue == ("queued", now + timedelta(minutes=60), "AI unavailable")
    assert retry_comment_status == "generation_retry"
    assert repo.claim_next_generation(now + timedelta(minutes=59)) is None
    claim = repo.claim_next_generation(now + timedelta(minutes=60))
    assert claim is not None

    terminal_outcome = repo.fail_generation(
        comment_id,
        claim_token=claim.claim_token,
        error_reason="AI unavailable",
        failed_at=now + timedelta(minutes=60),
        ai_provider="test",
        ai_model="test-model",
        article_context_status="without_article_text",
        retry_cooldown_minutes=60,
        max_attempts_per_comment=2,
    )

    assert terminal_outcome is GenerationFailureOutcome.TERMINAL
    with engine.connect() as conn:
        terminal_queue = conn.execute(
            select(
                ReplyGenerationQueueTable.state,
                ReplyGenerationQueueTable.last_error,
            ).where(ReplyGenerationQueueTable.comment_id == comment_id)
        ).one()
        terminal_comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
        replies = conn.execute(
            select(ReplyTable.status, ReplyTable.error_reason)
            .where(ReplyTable.comment_id == comment_id)
            .order_by(ReplyTable.id)
        ).all()
        publication_count = conn.execute(
            select(ReplyPublicationQueueTable.reply_id).join(
                ReplyTable, ReplyTable.id == ReplyPublicationQueueTable.reply_id
            ).where(ReplyTable.comment_id == comment_id)
        ).all()
    assert terminal_queue == ("completed", "AI unavailable")
    assert terminal_comment_status == "generation_error"
    assert replies == [("error", "AI unavailable"), ("error", "AI unavailable")]
    assert publication_count == []


@pytest.mark.parametrize("finish", ("complete", "skip", "fail"))
def test_stale_generation_claim_cannot_finish_a_newer_claim(repo, finish):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    comment_id = repo.upsert_comment(_make_comment(publication_id))
    assert repo.enqueue_generation(comment_id, queued_at=now)
    first_claim = repo.claim_next_generation(now)
    assert first_claim is not None
    second_claim = repo.claim_next_generation(now + timedelta(minutes=6))
    assert second_claim is not None
    assert first_claim.claim_token != second_claim.claim_token

    with pytest.raises(ValueError, match="claim"):
        if finish == "complete":
            repo.complete_generation(
                comment_id,
                claim_token=first_claim.claim_token,
                text="Готовый ответ",
                ai_provider="test",
                ai_model="test-model",
                article_context_status="article_text_used",
                created_at=now,
                is_cta_candidate=False,
            )
        elif finish == "skip":
            repo.skip_generation(
                comment_id,
                claim_token=first_claim.claim_token,
                reason="Не требует ответа",
                ai_provider="test",
                ai_model="test-model",
                article_context_status="without_article_text",
                created_at=now,
            )
        else:
            repo.fail_generation(
                comment_id,
                claim_token=first_claim.claim_token,
                error_reason="stale worker",
                failed_at=now,
                ai_provider="test",
                ai_model="test-model",
                article_context_status="without_article_text",
                retry_cooldown_minutes=60,
                max_attempts_per_comment=3,
            )


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


def test_article_context_is_persisted_on_the_publication(repo):
    publication_id = repo.upsert_publication(_make_publication())
    fetched_at = datetime(2026, 9, 10, 10, 0, 0)

    saved = repo.save_article_context(
        publication_id,
        text="Текст статьи",
        status="article_text_used",
        fetched_at=fetched_at,
    )

    assert saved.publication_id == publication_id
    assert saved.text == "Текст статьи"
    assert saved.status == "article_text_used"
    assert saved.fetched_at == fetched_at
    assert saved.content_hash == (
        "2be57bd9fdcaee96ade3bbc48abe37b274d99cfe06be4d8b34c880b04d42cd73"
    )
    assert repo.get_article_context(publication_id) == saved


def test_claimed_generated_reply_is_queued_once_for_publication(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(
        _make_comment(publication_id, fetched_at=now)
    )
    assert repo.enqueue_generation(comment_id, queued_at=now)
    generation = repo.claim_next_generation(now)
    assert generation is not None
    reply_id = repo.complete_generation(
        comment_id,
        claim_token=generation.claim_token,
        text="готово",
        ai_provider="test",
        ai_model="test-model",
        article_context_status="article_text_used",
        created_at=now,
        is_cta_candidate=False,
    )

    claimed = repo.claim_next_publication(now)

    assert claimed is not None
    assert claimed.reply_id == reply_id
    assert claimed.comment.id == comment_id
    assert claimed.text == "готово"
    assert repo.claim_next_publication(now) is None
    with engine.connect() as conn:
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.attempt_count,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
    assert queue == ("claimed", 1)


def test_claim_next_publication_claims_ready_reply_without_dom_filter(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="missing-from-dom", fetched_at=now)
    )
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)

    claimed = repo.claim_next_publication(now)

    assert claimed is not None
    assert claimed.reply_id == reply_id
    with engine.connect() as conn:
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.attempt_count,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
    assert queue == ("claimed", 1)


def test_expire_stale_publications_completes_only_queued_stale_jobs(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    cutoff = now - timedelta(days=1)

    claimed_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="claimed-stale", fetched_at=cutoff - timedelta(seconds=1))
    )
    claimed_reply_id = repo.save_reply(_make_reply(claimed_comment_id))
    assert repo.enqueue_publication(claimed_reply_id, created_at=now)
    publication_claim = repo.claim_next_publication(now)
    assert publication_claim is not None

    stale_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="queued-stale", fetched_at=cutoff - timedelta(seconds=1))
    )
    stale_reply_id = repo.save_reply(_make_reply(stale_comment_id))
    assert repo.enqueue_publication(stale_reply_id, created_at=now)

    fresh_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="fresh", fetched_at=cutoff)
    )
    fresh_reply_id = repo.save_reply(_make_reply(fresh_comment_id))
    assert repo.enqueue_publication(fresh_reply_id, created_at=now)

    expired = repo.expire_stale_publications(now, cutoff)

    assert expired == 1
    with engine.connect() as conn:
        queue_states = dict(
            conn.execute(
                select(
                    ReplyPublicationQueueTable.reply_id,
                    ReplyPublicationQueueTable.state,
                )
            ).all()
        )
        reply_states = dict(conn.execute(select(ReplyTable.id, ReplyTable.status)).all())
        comment_states = dict(
            conn.execute(select(CommentTable.id, CommentTable.status)).all()
        )
    assert queue_states == {
        claimed_reply_id: "claimed",
        stale_reply_id: "completed",
        fresh_reply_id: "queued",
    }
    assert reply_states == {
        claimed_reply_id: "generated",
        stale_reply_id: "skipped",
        fresh_reply_id: "generated",
    }
    assert comment_states == {
        claimed_comment_id: "new",
        stale_comment_id: "skipped",
        fresh_comment_id: "new",
    }

    claimed_fresh = repo.claim_next_publication(now)

    assert claimed_fresh is not None
    assert claimed_fresh.reply_id == fresh_reply_id


def test_stale_publication_claim_is_recovered_without_stealing_fresh_claim(repo):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(
        _make_comment(publication_id, fetched_at=now)
    )
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)
    publication_claim = repo.claim_next_publication(now)
    assert publication_claim is not None

    assert repo.claim_next_publication(now + timedelta(seconds=1)) is None
    recovered = repo.claim_next_publication(now + timedelta(hours=1))

    assert recovered is not None
    assert recovered.reply_id == reply_id
    with engine.connect() as conn:
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.attempt_count,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
    assert queue == ("claimed", 2)


@pytest.mark.parametrize("finish", ("complete", "fail"))
def test_stale_publication_claim_cannot_finish_a_newer_claim(repo, finish):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(
        _make_comment(publication_id, fetched_at=now)
    )
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)
    first_claim = repo.claim_next_publication(now)
    assert first_claim is not None
    second_claim = repo.claim_next_publication(now + timedelta(minutes=6))
    assert second_claim is not None
    assert first_claim.claim_token != second_claim.claim_token

    with pytest.raises(ValueError, match="claim"):
        if finish == "complete":
            repo.complete_publication(
                reply_id,
                claim_token=first_claim.claim_token,
                published_at=now,
            )
        else:
            repo.fail_publication(
                reply_id,
                claim_token=first_claim.claim_token,
                error_reason="stale worker",
                failed_at=now,
                retry_cooldown_minutes=60,
                max_attempts_per_reply=3,
            )


def test_publication_failure_retries_without_new_generation_and_ends_as_publication_error(
    repo, engine
):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(
        _make_comment(publication_id, fetched_at=now)
    )
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)
    publication_claim = repo.claim_next_publication(now)
    assert publication_claim is not None

    retry_outcome = repo.fail_publication(
        reply_id,
        claim_token=publication_claim.claim_token,
        error_reason="comment not in DOM",
        failed_at=now,
        retry_cooldown_minutes=60,
        max_attempts_per_reply=2,
    )
    assert retry_outcome.value == "retry"
    with engine.connect() as conn:
        retry_reply_status = conn.execute(
            select(ReplyTable.status).where(ReplyTable.id == reply_id)
        ).scalar_one()
        retry_comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
        retry_queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.last_error,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
    assert retry_reply_status == "generated"
    assert retry_comment_status == "publication_retry"
    assert retry_queue == ("queued", "comment not in DOM")
    assert repo.claim_next_publication(now + timedelta(minutes=59)) is None
    terminal_claim = repo.claim_next_publication(now + timedelta(minutes=60))
    assert terminal_claim is not None
    terminal_outcome = repo.fail_publication(
        reply_id,
        claim_token=terminal_claim.claim_token,
        error_reason="comment not in DOM",
        failed_at=now + timedelta(minutes=60),
        retry_cooldown_minutes=60,
        max_attempts_per_reply=2,
    )
    assert terminal_outcome.value == "terminal"

    with engine.connect() as conn:
        reply_status = conn.execute(
            select(ReplyTable.status).where(ReplyTable.id == reply_id)
        ).scalar_one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.last_error,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
        reply_count = conn.execute(text("SELECT COUNT(*) FROM replies")).scalar_one()
        generation_state = conn.execute(
            select(ReplyGenerationQueueTable.state).where(
                ReplyGenerationQueueTable.comment_id == comment_id
            )
        ).scalar_one_or_none()
    assert reply_status == "error"
    assert comment_status == "publication_error"
    assert queue == ("completed", "comment not in DOM")
    assert reply_count == 1
    assert generation_state is None
