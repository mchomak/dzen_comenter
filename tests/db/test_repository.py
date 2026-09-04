from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text

import dzen_commenter.db.repository as repository_module
from dzen_commenter.contracts.enums import BatchOutcomeKind, CommentStatus, ReplyStatus
from dzen_commenter.contracts.interfaces import CommentRepository
from dzen_commenter.contracts.models import BatchOutcome, Comment, Publication, Reply
from dzen_commenter.db.models import CommentBatchQueueTable
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
        "save_reply",
        "set_comment_status",
        "set_reply_status",
        "has_generated_reply",
        "has_published_reply",
        "is_own_reply",
        "count_published_replies_since",
        "count_ai_attempts_since",
        "count_cta_candidates_produced",
        "enqueue_batch_comment",
        "claim_next_batch",
        "save_batch_outcomes",
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


# --- Batch storage ---


def _enqueue(repo, comment_id, post_url, *, queued_at, cutover_at):
    assert repo.enqueue_batch_comment(
        comment_id,
        post_url,
        queued_at=queued_at,
        cutover_at=cutover_at,
    )


def test_enqueue_batch_comment_excludes_old_and_answered_comments(repo):
    pub_id = repo.upsert_publication(_make_publication())
    cutover = datetime(2026, 8, 28, 12, 0, 0)
    old_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="old", fetched_at=cutover - timedelta(seconds=1))
    )
    fresh_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="fresh", fetched_at=cutover)
    )
    answered_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="answered", fetched_at=cutover)
    )
    repo.save_reply(_make_reply(answered_id))

    assert not repo.enqueue_batch_comment(
        old_id, "http://post/1", queued_at=cutover, cutover_at=cutover
    )
    assert repo.enqueue_batch_comment(
        fresh_id, "http://post/1", queued_at=cutover, cutover_at=cutover
    )
    assert not repo.enqueue_batch_comment(
        fresh_id, "http://post/1", queued_at=cutover, cutover_at=cutover
    )
    assert not repo.enqueue_batch_comment(
        answered_id, "http://post/1", queued_at=cutover, cutover_at=cutover
    )


def test_enqueue_batch_comment_persists_datetime_literal(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    queued_at = datetime(2026, 8, 29, 9, 15, 30)
    comment_id = repo.upsert_comment(
        _make_comment(pub_id, fetched_at=queued_at)
    )

    assert repo.enqueue_batch_comment(
        comment_id,
        "http://post/1",
        queued_at=queued_at,
        cutover_at=queued_at,
    )

    with engine.connect() as conn:
        stored_queued_at = conn.execute(
            select(CommentBatchQueueTable.queued_at).where(
                CommentBatchQueueTable.comment_id == comment_id
            )
        ).scalar_one()

    assert stored_queued_at == queued_at


def test_claim_next_batch_extracts_joined_rows_in_order(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 8, 28, 12, 0, 0)
    cutover = now - timedelta(minutes=1)
    first_post_ids = [
        repo.upsert_comment(
            _make_comment(
                pub_id,
                dzen_id=f"first-{number}",
                post_url="http://post/first",
                fetched_at=now,
            )
        )
        for number in range(3)
    ]
    second_post_id = repo.upsert_comment(
        _make_comment(
            pub_id,
            dzen_id="second",
            post_url="http://post/second",
            fetched_at=now,
        )
    )
    for comment_id in first_post_ids:
        _enqueue(
            repo, comment_id, "http://post/first", queued_at=now, cutover_at=cutover
        )
    _enqueue(
        repo, second_post_id, "http://post/second", queued_at=now, cutover_at=cutover
    )

    batch = repo.claim_next_batch(
        now, max_comments=3, wait_hours=12, quota_remaining=10
    )

    assert batch is not None
    assert batch.post_url == "http://post/first"
    assert [(item.comment_id, item.item_no) for item in batch.items] == list(
        zip(first_post_ids, (1, 2, 3), strict=True)
    )
    with engine.connect() as conn:
        states = conn.execute(
            select(CommentBatchQueueTable.state)
            .where(CommentBatchQueueTable.comment_id.in_(first_post_ids))
            .order_by(CommentBatchQueueTable.comment_id)
        ).scalars().all()
    assert states == ["claimed", "claimed", "claimed"]


def test_claim_next_batch_claims_oldest_ready_comment_without_page_snapshot(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 8, 28, 12, 0, 0)
    cutover = now - timedelta(days=1)
    unavailable_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="unavailable", fetched_at=now)
    )
    available_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="available", fetched_at=now)
    )
    _enqueue(
        repo,
        unavailable_id,
        "http://post/1",
        queued_at=now - timedelta(hours=12),
        cutover_at=cutover,
    )
    _enqueue(
        repo,
        available_id,
        "http://post/1",
        queued_at=now - timedelta(hours=12),
        cutover_at=cutover,
    )

    batch = repo.claim_next_batch(
        now,
        max_comments=1,
        wait_hours=12,
        quota_remaining=1,
    )

    assert batch is not None
    assert [item.comment_id for item in batch.items] == [unavailable_id]
    with engine.connect() as conn:
        unavailable_state = conn.execute(
            select(CommentBatchQueueTable.state).where(
                CommentBatchQueueTable.comment_id == unavailable_id
            )
        ).scalar_one()
    assert unavailable_state == "claimed"


def test_claim_next_batch_waits_for_timeout_and_respects_quota(repo):
    pub_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 8, 28, 12, 0, 0)
    cutover = now - timedelta(days=1)
    recent_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="recent", fetched_at=now)
    )
    _enqueue(repo, recent_id, "http://post/1", queued_at=now, cutover_at=cutover)

    assert repo.claim_next_batch(
        now, max_comments=3, wait_hours=12, quota_remaining=3
    ) is None

    old_ids = [recent_id]
    for number in range(2):
        comment_id = repo.upsert_comment(
            _make_comment(pub_id, dzen_id=f"old-{number}", fetched_at=now)
        )
        old_ids.append(comment_id)
        _enqueue(
            repo,
            comment_id,
            "http://post/1",
            queued_at=now - timedelta(hours=12),
            cutover_at=cutover,
        )

    batch = repo.claim_next_batch(
        now, max_comments=5, wait_hours=12, quota_remaining=2
    )

    assert batch is not None
    assert len(batch.items) == 2
    assert {item.comment_id for item in batch.items} <= set(old_ids)


def test_claimed_comments_cannot_be_claimed_twice(repo):
    pub_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 8, 28, 12, 0, 0)
    cutover = now - timedelta(days=1)
    comment_ids = [
        repo.upsert_comment(
            _make_comment(pub_id, dzen_id=f"comment-{number}", fetched_at=now)
        )
        for number in range(6)
    ]
    for comment_id in comment_ids:
        _enqueue(repo, comment_id, "http://post/1", queued_at=now, cutover_at=cutover)

    first = repo.claim_next_batch(
        now, max_comments=3, wait_hours=12, quota_remaining=3
    )
    second = repo.claim_next_batch(
        now, max_comments=3, wait_hours=12, quota_remaining=3
    )

    assert first is not None and second is not None
    assert {item.comment_id for item in first.items}.isdisjoint(
        {item.comment_id for item in second.items}
    )


def test_save_batch_outcomes_is_atomic_and_counts_skips_and_errors(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 8, 28, 12, 0, 0)
    cutover = now - timedelta(days=1)
    comment_ids = [
        repo.upsert_comment(
            _make_comment(pub_id, dzen_id=f"outcome-{number}", fetched_at=now)
        )
        for number in range(3)
    ]
    for comment_id in comment_ids:
        _enqueue(repo, comment_id, "http://post/1", queued_at=now, cutover_at=cutover)
    batch = repo.claim_next_batch(now, max_comments=3, wait_hours=12, quota_remaining=3)
    assert batch is not None

    reply_ids = repo.save_batch_outcomes(
        batch.id,
        (
            BatchOutcome(comment_ids[0], 1, BatchOutcomeKind.REPLY, text="готово"),
            BatchOutcome(comment_ids[1], 2, BatchOutcomeKind.SKIP),
            BatchOutcome(
                comment_ids[2], 3, BatchOutcomeKind.ERROR, error_reason="bad output"
            ),
        ),
        ai_provider="test",
        ai_model="test-model",
        article_context_status="article_text_used",
        created_at=now,
        prompt_tokens=100,
        completion_tokens=30,
        retry_cooldown_minutes=60,
        max_attempts_per_comment=1,
    )

    assert len(reply_ids) == 3
    assert repo.count_ai_attempts_since(now - timedelta(seconds=1)) == 3
    with engine.begin() as conn:
        statuses = conn.execute(
            text("SELECT status FROM replies ORDER BY comment_id")
        ).scalars().all()
        batch_status = conn.execute(
            text("SELECT status FROM reply_batches WHERE id = :id"), {"id": batch.id}
        ).scalar_one()
    assert statuses == ["generated", "skipped", "error"]
    assert batch_status == "error"


def test_save_batch_outcomes_reads_core_table_results_by_mapping(repo, monkeypatch):
    pub_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 8, 28, 12, 0, 0)
    comment_id = repo.upsert_comment(
        _make_comment(pub_id, dzen_id="core-result", fetched_at=now)
    )
    _enqueue(
        repo,
        comment_id,
        "http://post/1",
        queued_at=now,
        cutover_at=now - timedelta(days=1),
    )
    batch = repo.claim_next_batch(now, max_comments=1, wait_hours=12, quota_remaining=1)
    assert batch is not None

    monkeypatch.setattr(
        repository_module, "ReplyBatchItemTable", repository_module.ReplyBatchItemTable.__table__
    )
    monkeypatch.setattr(
        repository_module,
        "CommentBatchQueueTable",
        repository_module.CommentBatchQueueTable.__table__,
    )

    reply_ids = repo.save_batch_outcomes(
        batch.id,
        (BatchOutcome(comment_id, 1, BatchOutcomeKind.REPLY, text="готово"),),
        ai_provider="test",
        ai_model="test-model",
        article_context_status="article_text_used",
        created_at=now,
        prompt_tokens=1,
        completion_tokens=1,
        retry_cooldown_minutes=60,
        max_attempts_per_comment=1,
    )

    assert len(reply_ids) == 1


def test_save_batch_outcomes_rejects_partial_data_without_writes(repo, engine):
    pub_id = repo.upsert_publication(_make_publication())
    now = datetime(2026, 8, 28, 12, 0, 0)
    cutover = now - timedelta(days=1)
    comment_ids = [
        repo.upsert_comment(
            _make_comment(pub_id, dzen_id=f"partial-{number}", fetched_at=now)
        )
        for number in range(2)
    ]
    for comment_id in comment_ids:
        _enqueue(repo, comment_id, "http://post/1", queued_at=now, cutover_at=cutover)
    batch = repo.claim_next_batch(now, max_comments=2, wait_hours=12, quota_remaining=2)
    assert batch is not None

    with pytest.raises(ValueError, match="claimed item order"):
        repo.save_batch_outcomes(
            batch.id,
            (BatchOutcome(comment_ids[0], 1, BatchOutcomeKind.REPLY, text="one"),),
            ai_provider="test",
            ai_model="test-model",
            article_context_status="article_text_used",
            created_at=now,
            prompt_tokens=1,
            completion_tokens=1,
            retry_cooldown_minutes=60,
            max_attempts_per_comment=1,
        )

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM replies")).scalar_one() == 0
