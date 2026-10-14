from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import event, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

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
    posted_at: datetime | None = datetime(2026, 1, 1, 12, 0, 0),
    fetched_at=datetime(2026, 1, 1, 12, 5, 0),
) -> Comment:
    return Comment(
        id=None,
        dzen_comment_id=dzen_id,
        publication_id=publication_id,
        author="alice",
        text=text,
        parent_comment_id=None,
        posted_at=posted_at,
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
    assert {
        "publications",
        "comments",
        "replies",
        "reply_publication_queue",
        "ai_call_reservations",
    } <= tables

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

    assert {column["name"] for column in insp.get_columns("ai_call_reservations")} == {
        "id",
        "reserved_at",
    }
    assert any(
        index["name"] == "ix_ai_call_reservations_reserved_at"
        for index in insp.get_indexes("ai_call_reservations")
    )


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


def test_ai_call_reservations_migration_backfills_legacy_attempt_before_new_failure(
    engine,
):
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(REPO_ROOT / "dzen_commenter" / "db" / "migrations")
    )
    command.downgrade(config, "0011_publication_claim_token")
    with engine.begin() as conn:
        now = conn.execute(
            text("SELECT CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow'")
        ).scalar_one()
        created_at = now - timedelta(minutes=10)
        publication_id = conn.execute(
            text(
                "INSERT INTO publications (dzen_publication_id, title, url) "
                "VALUES ('before-ai-ledger', 'Existing title', 'https://example.test') "
                "RETURNING id"
            )
        ).scalar_one()
        comment_id = conn.execute(
            text(
                "INSERT INTO comments (dzen_comment_id, publication_id, author, text, "
                "status) VALUES ('legacy-ai-comment', :publication_id, 'author', "
                "'comment text', 'generated') RETURNING id"
            ),
            {"publication_id": publication_id},
        ).scalar_one()
        reply_id = conn.execute(
            text(
                "INSERT INTO replies (comment_id, generated_text, status, created_at) "
                "VALUES (:comment_id, 'reply text', 'generated', :created_at) "
                "RETURNING id"
            ),
            {"comment_id": comment_id, "created_at": created_at},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO replies (comment_id, generated_text, status, created_at) "
                "VALUES (:comment_id, 'not counted', 'draft', :created_at), "
                "(:comment_id, 'too old', 'generated', :old_created_at)"
            ),
            {
                "comment_id": comment_id,
                "created_at": created_at,
                "old_created_at": now - timedelta(hours=1, seconds=1),
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as conn:
        reply_row = conn.execute(
            text("SELECT generated_text, status, created_at FROM replies WHERE id = :id"),
            {"id": reply_id},
        ).one()
        reservation_rows = conn.execute(
            text("SELECT reserved_at FROM ai_call_reservations ORDER BY reserved_at")
        ).all()

    assert reply_row == ("reply text", "generated", created_at)
    assert reservation_rows == [(created_at,)]
    migrated_repo = PostgresCommentRepository(engine)
    cutoff = now - timedelta(hours=1)
    assert migrated_repo.count_ai_attempts_since(cutoff) == 1

    # The first post-migration provider call reserves a slot but fails before a
    # Reply row is written. It must still consume the second hourly slot.
    failed_call_at = created_at + timedelta(seconds=1)
    assert migrated_repo.reserve_ai_call(failed_call_at, limit=2) is True
    assert migrated_repo.count_ai_attempts_since(cutoff) == 2
    assert migrated_repo.reserve_ai_call(failed_call_at + timedelta(seconds=1), limit=2) is False


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


def test_ai_attempt_counter_does_not_double_count_a_completed_new_call(repo):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 7, 31, 12, 0, 0)
    assert repo.reserve_ai_call(now, limit=10)
    reply_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="reply-without-reservation")
    )
    reply = _make_reply(reply_comment_id, status=ReplyStatus.GENERATED)
    reply.created_at = now
    repo.save_reply(reply)

    assert repo.count_ai_attempts_since(now - timedelta(hours=1)) == 1
    assert repo.reserve_ai_call(now, limit=1) is False


def test_ai_call_reservations_are_durable_and_enforce_rolling_hour_limit(repo, engine):
    now = datetime(2026, 9, 18, 12, 0, 0)
    restarted_repository = PostgresCommentRepository(engine)

    assert repo.reserve_ai_call(now, limit=2) is True
    assert restarted_repository.count_ai_attempts_since(now - timedelta(hours=1)) == 1
    assert restarted_repository.reserve_ai_call(
        now + timedelta(minutes=1), limit=2
    ) is True
    assert repo.reserve_ai_call(now + timedelta(minutes=2), limit=2) is False
    assert repo.reserve_ai_call(now + timedelta(hours=1, seconds=1), limit=2) is True


def test_ai_call_reservations_serialize_concurrent_workers(repo, engine):
    now = datetime(2026, 9, 18, 12, 0, 0)
    worker_count = 8
    start = Barrier(worker_count)

    def reserve_slot(_: int) -> bool:
        start.wait(timeout=5)
        return PostgresCommentRepository(engine).reserve_ai_call(now, limit=3)

    with ThreadPoolExecutor(max_workers=worker_count) as workers:
        outcomes = list(workers.map(reserve_slot, range(worker_count)))

    assert sum(outcomes) == 3
    assert repo.count_ai_attempts_since(now - timedelta(hours=1)) == 3


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
        "reconcile_work_window",
        "admit_recent_comments",
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


def test_release_generation_claim_requeues_and_ignores_stale_token(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    comment_id = repo.upsert_comment(_make_comment(publication_id))
    assert repo.enqueue_generation(comment_id, queued_at=now)

    first_claim = repo.claim_next_generation(now)
    assert first_claim is not None
    assert first_claim.attempt_count == 1

    repo.release_generation_claim(comment_id, claim_token=first_claim.claim_token)

    with engine.connect() as conn:
        released = conn.execute(
            select(
                ReplyGenerationQueueTable.state,
                ReplyGenerationQueueTable.attempt_count,
                ReplyGenerationQueueTable.claimed_at,
                ReplyGenerationQueueTable.claim_token,
            ).where(ReplyGenerationQueueTable.comment_id == comment_id)
        ).one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
    assert released == ("queued", 0, None, None)
    assert comment_status == "new"

    current_claim = repo.claim_next_generation(now + timedelta(seconds=1))
    assert current_claim is not None
    assert current_claim.comment.id == comment_id
    assert current_claim.attempt_count == 1
    assert current_claim.claim_token != first_claim.claim_token

    repo.release_generation_claim(comment_id, claim_token=first_claim.claim_token)

    with engine.connect() as conn:
        still_claimed = conn.execute(
            select(
                ReplyGenerationQueueTable.state,
                ReplyGenerationQueueTable.attempt_count,
                ReplyGenerationQueueTable.claimed_at,
                ReplyGenerationQueueTable.claim_token,
            ).where(ReplyGenerationQueueTable.comment_id == comment_id)
        ).one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
    assert still_claimed == (
        "claimed",
        1,
        now + timedelta(seconds=1),
        current_claim.claim_token,
    )
    assert comment_status == "generating"

    repo.release_generation_claim(comment_id, claim_token=current_claim.claim_token)
    assert repo.claim_next_generation(now + timedelta(seconds=2)).comment.id == comment_id


def test_generation_queue_claims_freshest_comments_with_fetched_at_fallback(repo):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    older_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="older-posted",
            posted_at=now - timedelta(minutes=10),
            fetched_at=now,
        )
    )
    newer_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="newer-posted",
            posted_at=now - timedelta(minutes=1),
            fetched_at=now,
        )
    )
    fallback_recent_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="fallback-recent",
            posted_at=None,
            fetched_at=now - timedelta(minutes=2),
        )
    )
    fallback_tie_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="fallback-tie",
            posted_at=None,
            fetched_at=now - timedelta(minutes=2),
        )
    )
    fallback_older_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="fallback-older",
            posted_at=None,
            fetched_at=now - timedelta(minutes=5),
        )
    )

    assert repo.enqueue_generation(older_id, queued_at=now - timedelta(minutes=2))
    assert repo.enqueue_generation(newer_id, queued_at=now)
    assert repo.enqueue_generation(fallback_recent_id, queued_at=now - timedelta(minutes=1))
    assert repo.enqueue_generation(fallback_tie_id, queued_at=now - timedelta(seconds=30))
    assert repo.enqueue_generation(fallback_older_id, queued_at=now - timedelta(seconds=10))

    claimed = repo.claim_next_generation(now)

    assert claimed is not None
    assert claimed.comment.id == newer_id
    assert repo.claim_next_generation(now).comment.id == fallback_recent_id
    assert repo.claim_next_generation(now).comment.id == fallback_tie_id
    assert repo.claim_next_generation(now).comment.id == fallback_older_id
    assert repo.claim_next_generation(now).comment.id == older_id


def test_reconcile_work_window_keeps_freshest_legacy_generation_jobs(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    stale_before = now - timedelta(days=2)
    comment_ids = []
    for age_minutes in range(31):
        comment_id = repo.upsert_comment(
            _make_comment(
                publication_id,
                dzen_id=f"window-{age_minutes:02d}",
                posted_at=(
                    None
                    if age_minutes == 1
                    else now - timedelta(minutes=age_minutes // 2)
                ),
                fetched_at=now,
            )
        )
        comment_ids.append(comment_id)
        assert repo.enqueue_generation(comment_id, queued_at=now)
    retry_at = now + timedelta(hours=1)
    with engine.begin() as conn:
        conn.execute(
            update(ReplyGenerationQueueTable).values(
                attempt_count=1,
                next_attempt_at=retry_at,
                last_error="temporary failure",
            )
        )

    snapshot = repo.reconcile_work_window(
        now=now, stale_before=stale_before, limit=30
    )

    with engine.connect() as conn:
        states = dict(
            conn.execute(
                select(ReplyGenerationQueueTable.comment_id, ReplyGenerationQueueTable.state)
            ).all()
        )
        waiting_retry = conn.execute(
            select(
                ReplyGenerationQueueTable.attempt_count,
                ReplyGenerationQueueTable.next_attempt_at,
                ReplyGenerationQueueTable.last_error,
            ).where(ReplyGenerationQueueTable.comment_id == comment_ids[30])
        ).one()
    assert snapshot.active_generation == 30
    assert snapshot.waiting_generation == 1
    assert {comment_id for comment_id, state in states.items() if state == "queued"} == set(
        comment_ids[:30]
    )
    assert {comment_id for comment_id, state in states.items() if state == "waiting"} == {
        comment_ids[30]
    }
    assert waiting_retry == (1, retry_at, "temporary failure")

    repeated = repo.reconcile_work_window(
        now=now, stale_before=stale_before, limit=30
    )

    assert repeated.active_generation == 30
    assert repeated.waiting_generation == 1


def test_admit_recent_comments_uses_free_slots_and_preserves_generation_retry(
    repo, engine
):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    retry_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="retry", posted_at=now, fetched_at=now)
    )
    assert repo.enqueue_generation(retry_comment_id, queued_at=now)
    claim = repo.claim_next_generation(now)
    assert claim is not None
    repo.fail_generation(
        retry_comment_id,
        claim_token=claim.claim_token,
        error_reason="temporary failure",
        failed_at=now,
        ai_provider="test",
        ai_model="test-model",
        article_context_status="without_article_text",
        retry_cooldown_minutes=60,
        max_attempts_per_comment=3,
    )
    retry_at = now + timedelta(minutes=60)

    existing_card = _make_comment(
        publication_id,
        dzen_id="retry",
        posted_at=now + timedelta(minutes=3),
        fetched_at=now,
    )
    freshest_unknown = _make_comment(
        publication_id,
        dzen_id="freshest-unseen",
        posted_at=now + timedelta(minutes=2),
        fetched_at=now,
    )
    second_unknown = _make_comment(
        publication_id,
        dzen_id="second-unseen",
        posted_at=now + timedelta(minutes=1),
        fetched_at=now,
    )
    duplicate_unknown = _make_comment(
        publication_id,
        dzen_id="freshest-unseen",
        posted_at=now,
        fetched_at=now,
    )

    admitted = repo.admit_recent_comments(
        [second_unknown, duplicate_unknown, existing_card, freshest_unknown],
        queued_at=now + timedelta(minutes=4),
        limit=2,
    )

    with engine.connect() as conn:
        ids = set(conn.execute(select(CommentTable.dzen_comment_id)).scalars())
        retry_queue = conn.execute(
            select(
                ReplyGenerationQueueTable.attempt_count,
                ReplyGenerationQueueTable.next_attempt_at,
                ReplyGenerationQueueTable.state,
            ).where(ReplyGenerationQueueTable.comment_id == retry_comment_id)
        ).one()
    assert admitted == 1
    assert ids == {"retry", "freshest-unseen"}
    assert retry_queue == (1, retry_at, "queued")


def test_saved_publications_fill_window_before_generation_and_wait_for_slots(
    repo, engine
):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    saved_reply_ids = []
    for index in range(31):
        comment_id = repo.upsert_comment(
            _make_comment(
                publication_id,
                dzen_id=f"saved-{index:02d}",
                posted_at=now - timedelta(minutes=index),
                fetched_at=now,
            )
        )
        saved_reply_ids.append(repo.save_reply(_make_reply(comment_id)))
    generation_comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="fresh-generation",
            posted_at=now,
            fetched_at=now,
        )
    )
    assert repo.enqueue_generation(generation_comment_id, queued_at=now)

    snapshot = repo.reconcile_work_window(
        now=now, stale_before=now - timedelta(days=2), limit=30
    )

    with engine.connect() as conn:
        publication_states = dict(
            conn.execute(
                select(
                    ReplyPublicationQueueTable.reply_id,
                    ReplyPublicationQueueTable.state,
                )
            ).all()
        )
    assert snapshot.active_publication == 30
    assert snapshot.waiting_publication == 1
    assert snapshot.generated_waiting_publication == 1
    assert snapshot.waiting_generation == 1
    assert sum(state == "queued" for state in publication_states.values()) == 30
    assert sum(state == "waiting" for state in publication_states.values()) == 1
    assert repo.claim_next_generation(now) is None

    first = repo.claim_next_publication(now)
    assert first is not None
    assert first.reply_id == saved_reply_ids[0]
    repo.complete_publication(
        first.reply_id,
        claim_token=first.claim_token,
        published_at=now,
    )
    repeated = repo.reconcile_work_window(
        now=now, stale_before=now - timedelta(days=2), limit=30
    )
    with engine.connect() as conn:
        waiting_ids = conn.execute(
            select(ReplyPublicationQueueTable.reply_id).where(
                ReplyPublicationQueueTable.state == "waiting"
            )
        ).scalars().all()
    assert repeated.active_publication == 30
    assert repeated.waiting_publication == 0
    assert repeated.waiting_generation == 1
    assert waiting_ids == []


def test_generation_to_publication_transition_keeps_one_unique_window_slot(repo):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    first_comment_id = repo.upsert_comment(
        _make_comment(publication_id, dzen_id="first", posted_at=now, fetched_at=now)
    )
    second_comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="second",
            posted_at=now - timedelta(minutes=1),
            fetched_at=now,
        )
    )
    assert repo.enqueue_generation(first_comment_id, queued_at=now)
    assert repo.enqueue_generation(second_comment_id, queued_at=now)
    before = repo.reconcile_work_window(
        now=now, stale_before=now - timedelta(days=2), limit=1
    )
    assert before.active_generation == 1
    assert before.waiting_generation == 1

    claim = repo.claim_next_generation(now)
    assert claim is not None
    assert claim.comment.id == first_comment_id
    reply_id = repo.complete_generation(
        first_comment_id,
        claim_token=claim.claim_token,
        text="saved reply",
        ai_provider="test",
        ai_model="test-model",
        article_context_status="without_article_text",
        created_at=now,
        is_cta_candidate=False,
    )

    after = repo.reconcile_work_window(
        now=now, stale_before=now - timedelta(days=2), limit=1
    )

    assert after.active_generation == 0
    assert after.active_publication == 1
    assert after.waiting_generation == 1
    publication = repo.claim_next_publication(now)
    assert publication is not None
    assert publication.reply_id == reply_id


def test_reconcile_expires_stale_new_and_only_expires_claim_after_lease(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    stale_before = now - timedelta(days=1)
    claimed_comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="old-claimed",
            posted_at=now - timedelta(days=2),
            fetched_at=now - timedelta(days=2),
        )
    )
    assert repo.enqueue_generation(claimed_comment_id, queued_at=now)
    claim = repo.claim_next_generation(now)
    assert claim is not None
    new_comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="old-new",
            posted_at=now - timedelta(days=2),
            fetched_at=now - timedelta(days=2),
        )
    )
    queued_comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="old-queued",
            posted_at=now - timedelta(days=2),
            fetched_at=now - timedelta(days=2),
        )
    )
    assert repo.enqueue_generation(queued_comment_id, queued_at=now)
    waiting_comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="old-waiting",
            posted_at=now - timedelta(days=2),
            fetched_at=now - timedelta(days=2),
        )
    )
    assert repo.enqueue_generation(waiting_comment_id, queued_at=now)
    with engine.begin() as conn:
        conn.execute(
            update(ReplyGenerationQueueTable)
            .where(ReplyGenerationQueueTable.comment_id == waiting_comment_id)
            .values(state="waiting")
        )
    stale_publication_comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="old-publication",
            posted_at=now - timedelta(days=2),
            fetched_at=now - timedelta(days=2),
        )
    )
    stale_reply_id = repo.save_reply(_make_reply(stale_publication_comment_id))
    assert repo.enqueue_publication(stale_reply_id, created_at=now)

    while_claim_is_live = repo.reconcile_work_window(
        now=now + timedelta(minutes=1), stale_before=stale_before, limit=10
    )
    assert while_claim_is_live.active_generation == 1
    assert while_claim_is_live.expired_generation == 3
    assert while_claim_is_live.expired_publication == 1

    after_lease = repo.reconcile_work_window(
        now=now + timedelta(minutes=6), stale_before=stale_before, limit=10
    )

    with engine.connect() as conn:
        queue = conn.execute(
            select(
                ReplyGenerationQueueTable.state,
                ReplyGenerationQueueTable.claim_token,
            ).where(ReplyGenerationQueueTable.comment_id == claimed_comment_id)
        ).one()
        replies = conn.execute(
            select(ReplyTable.comment_id, ReplyTable.status, ReplyTable.error_reason)
            .where(
                ReplyTable.comment_id.in_(
                    (
                        claimed_comment_id,
                        new_comment_id,
                        queued_comment_id,
                        waiting_comment_id,
                        stale_publication_comment_id,
                    )
                )
            )
            .order_by(ReplyTable.comment_id)
        ).all()
        statuses = dict(
            conn.execute(
                select(CommentTable.id, CommentTable.status).where(
                    CommentTable.id.in_(
                        (
                            claimed_comment_id,
                            new_comment_id,
                            queued_comment_id,
                            waiting_comment_id,
                            stale_publication_comment_id,
                        )
                    )
                )
            ).all()
        )
    assert after_lease.expired_generation == 1
    assert after_lease.expired_publication == 0
    assert queue == ("completed", None)
    assert len(replies) == 5
    assert all(
        status == "skipped"
        and error_reason == "comment exceeded the configured age limit"
        for _, status, error_reason in replies
    )
    assert statuses == {
        claimed_comment_id: "skipped",
        new_comment_id: "skipped",
        queued_comment_id: "skipped",
        waiting_comment_id: "skipped",
        stale_publication_comment_id: "skipped",
    }


def test_reconcile_keeps_unknown_submission_unconfirmed_after_lease(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 18, 12, 0, 0)
    comment_id = repo.upsert_comment(
        _make_comment(
            publication_id,
            dzen_id="old-submission",
            posted_at=now - timedelta(days=2),
            fetched_at=now - timedelta(days=2),
        )
    )
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)
    claim = repo.claim_next_publication(now)
    assert claim is not None
    repo.mark_publication_submitting(reply_id, claim_token=claim.claim_token)
    stale_before = now - timedelta(days=1)

    before_lease_expiry = repo.reconcile_work_window(
        now=now + timedelta(hours=1), stale_before=stale_before, limit=1
    )
    assert before_lease_expiry.active_publication == 1
    assert before_lease_expiry.expired_publication == 0

    after_lease_expiry = repo.reconcile_work_window(
        now=now + timedelta(hours=2, minutes=1),
        stale_before=stale_before,
        limit=1,
    )

    with engine.connect() as conn:
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.last_error,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
        reply = conn.execute(
            select(ReplyTable.status, ReplyTable.error_reason).where(
                ReplyTable.id == reply_id
            )
        ).one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
    assert after_lease_expiry.active_publication == 0
    assert after_lease_expiry.expired_publication == 0
    assert queue == (
        "completed",
        "publication outcome unknown after worker interruption",
    )
    assert reply == (
        "unconfirmed",
        "publication outcome unknown after worker interruption",
    )
    assert comment_status == "publication_unconfirmed"
    assert repo.claim_next_publication(now + timedelta(hours=2, minutes=1)) is None


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


def test_complete_generation_rolls_back_when_publication_row_insert_fails(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    comment_id = repo.upsert_comment(_make_comment(publication_id))
    now = datetime(2026, 9, 18, 12, 0, 0)
    assert repo.enqueue_generation(comment_id, queued_at=now)
    claim = repo.claim_next_generation(now)
    assert claim is not None

    def reject_publication_insert(conn, cursor, statement, parameters, context, many):
        if "insert into reply_publication_queue" in statement.lower():
            raise RuntimeError("forced publication row failure")

    event.listen(engine, "before_cursor_execute", reject_publication_insert)
    try:
        with pytest.raises(RuntimeError, match="forced publication row failure"):
            repo.complete_generation(
                comment_id,
                claim_token=claim.claim_token,
                text="saved reply",
                ai_provider="test",
                ai_model="test-model",
                article_context_status="without_article_text",
                created_at=now,
                is_cta_candidate=False,
            )
    finally:
        event.remove(engine, "before_cursor_execute", reject_publication_insert)

    with engine.connect() as conn:
        reply_count = conn.execute(
            select(ReplyTable.id).where(ReplyTable.comment_id == comment_id)
        ).scalars().all()
        generation_state = conn.execute(
            select(ReplyGenerationQueueTable.state).where(
                ReplyGenerationQueueTable.comment_id == comment_id
            )
        ).scalar_one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
    assert reply_count == []
    assert generation_state == "claimed"
    assert comment_status == "generating"


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
        claimed_comment_id: "publishing",
        stale_comment_id: "skipped",
        fresh_comment_id: "new",
    }

    claimed_fresh = repo.claim_next_publication(now)

    assert claimed_fresh is not None
    assert claimed_fresh.reply_id == fresh_reply_id


def test_stale_publication_claim_is_recovered_without_stealing_fresh_claim(repo, engine):
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
    assert repo.claim_next_publication(now + timedelta(hours=1)) is None
    recovered = repo.claim_next_publication(now + timedelta(hours=2, minutes=1))

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
    assert repo.claim_next_publication(now + timedelta(minutes=6)) is None
    second_claim = repo.claim_next_publication(now + timedelta(hours=2, minutes=1))
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

    repo.mark_publication_submitting(reply_id, claim_token=publication_claim.claim_token)

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
                ReplyPublicationQueueTable.next_attempt_at,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
    assert retry_reply_status == "generated"
    assert retry_comment_status == "publication_retry"
    assert retry_queue == ("queued", "comment not in DOM", now + timedelta(minutes=60))
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


def test_unconfirmed_publication_is_atomic_terminal_and_rejects_stale_token(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(_make_comment(publication_id, fetched_at=now))
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)
    claim = repo.claim_next_publication(now)
    assert claim is not None

    repo.mark_publication_submitting(reply_id, claim_token=claim.claim_token)

    repo.mark_publication_unconfirmed(
        reply_id, claim_token=claim.claim_token, reason="public confirmation timed out"
    )

    with engine.connect() as conn:
        reply = conn.execute(
            select(ReplyTable.status, ReplyTable.published_at, ReplyTable.error_reason)
            .where(ReplyTable.id == reply_id)
        ).one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.next_attempt_at,
                ReplyPublicationQueueTable.last_error,
                ReplyPublicationQueueTable.claim_token,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
    assert reply == ("unconfirmed", None, "public confirmation timed out")
    assert comment_status == "publication_unconfirmed"
    assert queue == ("completed", None, "public confirmation timed out", None)
    assert repo.claim_next_publication(now + timedelta(hours=3)) is None
    with pytest.raises(ValueError, match="claim"):
        repo.mark_publication_unconfirmed(
            reply_id, claim_token=claim.claim_token, reason="stale worker"
        )


def test_expired_submitting_claim_becomes_unconfirmed_without_reclaim(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(_make_comment(publication_id, fetched_at=now))
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)
    claim = repo.claim_next_publication(now)
    assert claim is not None
    with pytest.raises(ValueError, match="claim"):
        repo.mark_publication_submitting(reply_id, claim_token="stale")
    repo.mark_publication_submitting(reply_id, claim_token=claim.claim_token)

    assert repo.claim_next_publication(now + timedelta(hours=1)) is None
    assert repo.claim_next_publication(now + timedelta(hours=2, minutes=1)) is None
    with engine.connect() as conn:
        reply = conn.execute(
            select(ReplyTable.status, ReplyTable.published_at, ReplyTable.error_reason)
            .where(ReplyTable.id == reply_id)
        ).one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.attempt_count,
                ReplyPublicationQueueTable.claim_token,
            ).where(ReplyPublicationQueueTable.reply_id == reply_id)
        ).one()
    assert reply == (
        "unconfirmed",
        None,
        "publication outcome unknown after worker interruption",
    )
    assert comment_status == "publication_unconfirmed"
    assert queue == ("completed", 1, None)
    with pytest.raises(ValueError, match="claim"):
        repo.complete_publication(
            reply_id, claim_token=claim.claim_token, published_at=now
        )


def test_confirmed_submitting_claim_marks_reply_and_comment_published(repo, engine):
    publication_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 9, 10, 10, 0, 0)
    comment_id = repo.upsert_comment(_make_comment(publication_id, fetched_at=now))
    reply_id = repo.save_reply(_make_reply(comment_id))
    assert repo.enqueue_publication(reply_id, created_at=now)
    claim = repo.claim_next_publication(now)
    assert claim is not None
    repo.mark_publication_submitting(reply_id, claim_token=claim.claim_token)

    repo.complete_publication(reply_id, claim_token=claim.claim_token, published_at=now)

    with engine.connect() as conn:
        reply = conn.execute(
            select(ReplyTable.status, ReplyTable.published_at).where(ReplyTable.id == reply_id)
        ).one()
        comment_status = conn.execute(
            select(CommentTable.status).where(CommentTable.id == comment_id)
        ).scalar_one()
        queue_state = conn.execute(
            select(ReplyPublicationQueueTable.state).where(
                ReplyPublicationQueueTable.reply_id == reply_id
            )
        ).scalar_one()
    assert reply == ("published", now)
    assert comment_status == "published"
    assert queue_state == "completed"
