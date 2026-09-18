import ast
import inspect
import pathlib
from datetime import datetime, timedelta

from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.models import Publication
from dzen_commenter.orchestrator import OrchestratorLoop


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_orchestrator_loop_has_only_single_prompt_dependencies():
    signature = inspect.signature(OrchestratorLoop.__init__)
    assert list(signature.parameters) == [
        "self", "settings", "repository", "ai_provider", "prompt_builder",
        "session", "page", "notifier", "auth_assistant", "classify_reply_type",
        "is_cta_candidate_title", "runtime_config", "sleep_fn",
    ]
    tree = ast.parse((REPO_ROOT / "dzen_commenter/orchestrator/loop.py").read_text(encoding="utf-8"))
    assert all("batch" not in node.name.casefold() for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))


def test_run_cycle_generates_and_publishes_one_cleaned_reply_with_article_context(loop_factory, comment_factory):
    comment = comment_factory(1)
    comment.post_url = "https://dzen.example.test/article"
    harness = loop_factory(comments=[comment], ai_responses=["Ответ: Готовый ответ"], settings_overrides={"AUTO_PUBLISH": True})
    harness.page.article_text_by_url[comment.post_url] = "Очищенный текст статьи"

    harness.loop.run_cycle()

    reply = harness.repository.replies[1]
    assert reply.generated_text == "author-1, готовый ответ"
    assert reply.status is ReplyStatus.PUBLISHED
    assert reply.article_context_status == "article_text_used"
    assert harness.repository.generation_queue[1]["state"] == "completed"
    assert harness.repository.publication_queue[1]["state"] == "completed"
    assert harness.prompt_builder.contexts[0].article_text == "Очищенный текст статьи"


def test_run_cycle_persists_eligible_comments_through_atomic_generation_seam(
    loop_factory, comment_factory
):
    comment = comment_factory(1)
    harness = loop_factory(comments=[comment], ai_responses=["Ответ"])

    harness.loop.run_cycle()

    assert harness.repository.upsert_eligible_comment_calls == [comment]


def test_rescrape_does_not_replace_an_active_generation_status_with_skipped(
    loop_factory, comment_factory, monkeypatch
):
    from dzen_commenter.orchestrator import loop as loop_module

    now = datetime(2026, 9, 18, 12, 0, 0)
    monkeypatch.setattr(loop_module, "moscow_now", lambda: now)
    rescraped = comment_factory(1)
    rescraped.posted_at = now - timedelta(days=31)
    harness = loop_factory(comments=[rescraped])
    publication_id = harness.repository.upsert_publication(
        Publication(
            id=None,
            dzen_publication_id=harness.settings.COMMENTS_URL,
            title=harness.settings.COMMENTS_URL,
            url=harness.settings.COMMENTS_URL,
        )
    )
    saved = comment_factory(1)
    saved.publication_id = publication_id
    saved.posted_at = rescraped.posted_at
    comment_id = harness.repository.upsert_eligible_comment(saved, queued_at=now)
    assert harness.repository.claim_next_generation(now) is not None

    harness.loop.run_cycle()

    assert harness.repository.comments[comment_id].status is CommentStatus.GENERATING


def test_protocol_only_output_retries_generation_without_a_publication_job(loop_factory, comment_factory, monkeypatch):
    from dzen_commenter.orchestrator import loop as loop_module

    now = datetime(2026, 9, 18, 12, 0, 0)
    monkeypatch.setattr(loop_module, "moscow_now", lambda: now)
    harness = loop_factory(comments=[comment_factory(1)], ai_responses=["Ответ: SKIP"])

    harness.loop.run_cycle()

    assert harness.repository.comments[1].status is CommentStatus.GENERATION_RETRY
    assert harness.repository.generation_queue[1]["next_attempt_at"] == now + timedelta(minutes=60)
    assert harness.repository.publication_queue == {}
    assert harness.repository.replies[1].status is ReplyStatus.ERROR


def test_explicit_skip_is_terminal_without_publication(loop_factory, comment_factory):
    harness = loop_factory(comments=[comment_factory(1)], ai_responses=["SKIP"])

    harness.loop.run_cycle()

    assert harness.repository.comments[1].status is CommentStatus.SKIPPED
    assert harness.repository.replies[1].status is ReplyStatus.SKIPPED
    assert harness.repository.publication_queue == {}


def test_publication_retry_preserves_generated_text_and_never_regenerates(loop_factory, comment_factory, monkeypatch):
    from dzen_commenter.orchestrator import loop as loop_module

    clock = {"now": datetime(2026, 9, 18, 12, 0, 0)}
    monkeypatch.setattr(loop_module, "moscow_now", lambda: clock["now"])
    harness = loop_factory(comments=[comment_factory(1)], ai_responses=["Готовый ответ"], settings_overrides={"AUTO_PUBLISH": True})
    original_publish = harness.page.publish_reply
    harness.page.publish_reply = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("DOM changed"))

    harness.loop.run_cycle()
    saved_text = harness.repository.replies[1].generated_text
    assert harness.repository.comments[1].status is CommentStatus.PUBLICATION_RETRY

    clock["now"] += timedelta(minutes=60)
    harness.page.publish_reply = original_publish
    harness.loop.run_cycle()

    assert len(harness.ai_provider.calls) == 1
    assert harness.repository.replies[1].generated_text == saved_text
    assert harness.repository.replies[1].status is ReplyStatus.PUBLISHED


def test_article_context_is_cached_for_the_next_single_comment(loop_factory, comment_factory):
    first, second = comment_factory(1), comment_factory(2)
    first.post_url = second.post_url = "https://dzen.example.test/article"
    harness = loop_factory(comments=[first], ai_responses=["Первый"])
    harness.page.article_text_by_url[first.post_url] = "Очищенный текст"

    harness.loop.run_cycle()
    harness.page.comments = [second]
    harness.page.article_text_by_url[first.post_url] = "Не должен читаться"
    harness.loop.run_cycle()

    assert harness.page.article_text_urls == [first.post_url]
    assert [context.article_text for context in harness.prompt_builder.contexts] == ["Очищенный текст", "Очищенный текст"]


def test_run_cycle_recovers_a_stored_new_comment_without_a_dom_snapshot(loop_factory, comment_factory):
    harness = loop_factory(comments=[], ai_responses=["Recovered"])
    publication_id = harness.repository.upsert_publication(
        Publication(
            id=None,
            dzen_publication_id=harness.settings.COMMENTS_URL,
            title=harness.settings.COMMENTS_URL,
            url=harness.settings.COMMENTS_URL,
        )
    )
    saved_comment = comment_factory(1)
    saved_comment.publication_id = publication_id
    saved_id = harness.repository.upsert_comment(saved_comment)

    harness.loop.run_cycle()

    assert harness.page.fetch_calls == 1
    assert harness.repository.replies[1].comment_id == saved_id
    assert harness.repository.comments[saved_id].status is CommentStatus.GENERATED
