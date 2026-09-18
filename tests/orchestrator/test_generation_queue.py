from datetime import datetime, timedelta

import pytest

from dzen_commenter.contracts.enums import (
    CommentStatus,
    GenerationFailureOutcome,
    PublicationFailureOutcome,
    ReplyStatus,
)
from dzen_commenter.contracts.models import Publication


def test_fake_repository_persists_eligible_comment_with_generation_job(
    comment_factory, loop_factory
):
    repository = loop_factory().repository
    publication_id = repository.upsert_publication(
        Publication(id=None, dzen_publication_id="publication", title="T", url="http://x")
    )
    comment = comment_factory(1)
    comment.publication_id = publication_id
    now = datetime(2026, 9, 18, 12, 0, 0)

    comment_id = repository.upsert_eligible_comment(comment, queued_at=now)

    claimed = repository.claim_next_generation(now)
    assert claimed is not None
    assert claimed.comment.id == comment_id
    assert claimed.comment.dzen_comment_id == "comment-1"
    assert claimed.attempt_count == 1


def test_fake_repository_recovers_saved_new_comments_into_generation_queue(
    comment_factory, loop_factory
):
    repository = loop_factory().repository
    publication_id = repository.upsert_publication(
        Publication(id=None, dzen_publication_id="publication", title="T", url="http://x")
    )
    comment = comment_factory(1)
    comment.publication_id = publication_id
    comment_id = repository.upsert_comment(comment)
    now = datetime(2026, 9, 18, 12, 0, 0)

    assert hasattr(repository, "enqueue_pending_generations")
    assert repository.enqueue_pending_generations(queued_at=now) == 1
    assert repository.enqueue_pending_generations(queued_at=now) == 0
    claimed = repository.claim_next_generation(now)
    assert claimed is not None
    assert claimed.comment.id == comment_id


@pytest.mark.parametrize("finish", ("complete", "skip", "fail"))
def test_fake_repository_rejects_an_expired_generation_claim(
    finish, comment_factory, loop_factory
):
    repository = loop_factory().repository
    publication_id = repository.upsert_publication(
        Publication(id=None, dzen_publication_id="publication", title="T", url="http://x")
    )
    comment = comment_factory(1)
    comment.publication_id = publication_id
    now = datetime(2026, 9, 18, 12, 0, 0)
    comment_id = repository.upsert_eligible_comment(comment, queued_at=now)
    first_claim = repository.claim_next_generation(now)

    assert first_claim is not None
    assert hasattr(first_claim, "claim_token")
    later_claim = repository.claim_next_generation(now + timedelta(minutes=6))
    assert later_claim is not None

    with pytest.raises(ValueError, match="claim"):
        if finish == "complete":
            repository.complete_generation(
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
            repository.skip_generation(
                comment_id,
                claim_token=first_claim.claim_token,
                reason="Не требует ответа",
                ai_provider="test",
                ai_model="test-model",
                article_context_status="without_article_text",
                created_at=now,
            )
        else:
            repository.fail_generation(
                comment_id,
                claim_token=first_claim.claim_token,
                error_reason="AI unavailable",
                failed_at=now,
                ai_provider="test",
                ai_model="test-model",
                article_context_status="without_article_text",
                retry_cooldown_minutes=60,
                max_attempts_per_comment=2,
            )


def test_fake_repository_retries_generation_without_a_publication_job(
    comment_factory, loop_factory,
):
    repository = loop_factory().repository
    publication_id = repository.upsert_publication(
        Publication(id=None, dzen_publication_id="publication", title="T", url="http://x")
    )
    comment = comment_factory(1)
    comment.publication_id = publication_id
    comment_id = repository.upsert_comment(comment)
    now = datetime(2026, 9, 18, 12, 0, 0)

    assert repository.enqueue_generation(comment_id, queued_at=now)
    claim = repository.claim_next_generation(now)
    assert claim is not None

    outcome = repository.fail_generation(
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

    assert outcome is GenerationFailureOutcome.RETRY
    assert repository.comments[comment_id].status is CommentStatus.GENERATION_RETRY
    assert repository.replies[1].status is ReplyStatus.ERROR
    assert repository.publication_queue == {}
    assert repository.claim_next_generation(now + timedelta(minutes=59)) is None
    recovered = repository.claim_next_generation(now + timedelta(minutes=60))
    assert recovered is not None
    assert recovered.comment.id == comment_id
    assert recovered.attempt_count == 2


def test_fake_repository_preserves_generated_text_for_publication_retry(
    comment_factory, loop_factory
):
    repository = loop_factory().repository
    publication_id = repository.upsert_publication(
        Publication(id=None, dzen_publication_id="publication", title="T", url="http://x")
    )
    comment = comment_factory(1)
    comment.publication_id = publication_id
    comment_id = repository.upsert_comment(comment)
    now = datetime(2026, 9, 18, 12, 0, 0)
    assert repository.enqueue_generation(comment_id, queued_at=now)
    claim = repository.claim_next_generation(now)
    assert claim is not None
    reply_id = repository.complete_generation(
        comment_id,
        claim_token=claim.claim_token,
        text="Сохранённый ответ",
        ai_provider="test",
        ai_model="test-model",
        article_context_status="article_text_used",
        created_at=now,
        is_cta_candidate=False,
    )
    publication_claim = repository.claim_next_publication(now)
    assert publication_claim is not None

    outcome = repository.fail_publication(
        reply_id,
        claim_token=publication_claim.claim_token,
        error_reason="DOM changed",
        failed_at=now,
        retry_cooldown_minutes=60,
        max_attempts_per_reply=2,
    )

    assert outcome is PublicationFailureOutcome.RETRY
    assert repository.replies[reply_id].generated_text == "Сохранённый ответ"
    assert repository.replies[reply_id].status is ReplyStatus.GENERATED
    assert repository.comments[comment_id].status is CommentStatus.PUBLICATION_RETRY
    assert repository.claim_next_generation(now + timedelta(hours=2)) is None


@pytest.mark.parametrize("finish", ("complete", "fail"))
def test_fake_repository_rejects_an_expired_publication_claim(
    finish, comment_factory, loop_factory
):
    repository = loop_factory().repository
    publication_id = repository.upsert_publication(
        Publication(id=None, dzen_publication_id="publication", title="T", url="http://x")
    )
    comment = comment_factory(1)
    comment.publication_id = publication_id
    now = datetime(2026, 9, 18, 12, 0, 0)
    comment_id = repository.upsert_comment(comment)
    assert repository.enqueue_generation(comment_id, queued_at=now)
    generation_claim = repository.claim_next_generation(now)
    assert generation_claim is not None
    reply_id = repository.complete_generation(
        comment_id,
        claim_token=generation_claim.claim_token,
        text="Сохранённый ответ",
        ai_provider="test",
        ai_model="test-model",
        article_context_status="article_text_used",
        created_at=now,
        is_cta_candidate=False,
    )
    first_claim = repository.claim_next_publication(now)
    assert first_claim is not None
    second_claim = repository.claim_next_publication(now + timedelta(minutes=6))
    assert second_claim is not None
    assert first_claim.claim_token != second_claim.claim_token

    with pytest.raises(ValueError, match="claim"):
        if finish == "complete":
            repository.complete_publication(
                reply_id,
                claim_token=first_claim.claim_token,
                published_at=now,
            )
        else:
            repository.fail_publication(
                reply_id,
                claim_token=first_claim.claim_token,
                error_reason="stale worker",
                failed_at=now,
                retry_cooldown_minutes=60,
                max_attempts_per_reply=3,
            )
