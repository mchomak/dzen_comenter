import ast
import inspect
import pathlib
from datetime import datetime, timedelta

from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.models import Reply
from dzen_commenter.orchestrator import OrchestratorLoop

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ORCHESTRATOR_ROOT = REPO_ROOT / "dzen_commenter" / "orchestrator"


def test_orchestrator_loop_import_and_di_signature():
    signature = inspect.signature(OrchestratorLoop.__init__)
    expected = [
        "self",
        "settings",
        "repository",
        "ai_provider",
        "prompt_builder",
        "session",
        "page",
        "notifier",
        "auth_assistant",
        "classify_reply_type",
        "is_cta_candidate_title",
        "runtime_config",
        "sleep_fn",
    ]

    assert list(signature.parameters) == expected
    for name in expected[1:]:
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["sleep_fn"].default is not inspect.Signature.empty


def test_orchestrator_has_no_direct_imports_from_concrete_layers():
    forbidden_prefixes = (
        "dzen_commenter.db",
        "dzen_commenter.ai",
        "dzen_commenter.prompt",
        "dzen_commenter.browser",
        "dzen_commenter.dzen",
        "dzen_commenter.monitoring",
        "dzen_commenter.auth",
    )
    forbidden_root_names = {
        "db",
        "ai",
        "prompt",
        "browser",
        "dzen",
        "monitoring",
        "auth",
    }
    offenders = []

    for path in ORCHESTRATOR_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_prefixes):
                        offenders.append((path.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(forbidden_prefixes):
                    offenders.append((path.name, module))
                if module == "dzen_commenter":
                    for alias in node.names:
                        if alias.name in forbidden_root_names:
                            offenders.append((path.name, f"{module}.{alias.name}"))

    assert offenders == []


def test_run_cycle_enters_drafts_without_auto_publish(
    loop_factory,
    comment_factory,
):
    comments = [comment_factory(1), comment_factory(2)]
    harness = loop_factory(comments=comments)

    harness.loop.run_cycle()

    assert len(harness.repository.upsert_publication_calls) == 1
    assert len(harness.repository.replies) == 2
    assert all(
        comment.status == CommentStatus.ANSWERED
        for comment in harness.repository.comments.values()
    )
    assert all(
        reply.status == ReplyStatus.GENERATED
        for reply in harness.repository.replies.values()
    )
    assert harness.page.publish_calls == [
        (harness.repository.comments[1], "generated reply", False),
        (harness.repository.comments[2], "generated reply", False),
    ]
    assert len(harness.prompt_builder.contexts) == 2
    assert harness.classify_reply_type.calls == [
        (harness.settings.COMMENTS_URL, "comment text 1"),
        (harness.settings.COMMENTS_URL, "comment text 2"),
    ]


def test_run_cycle_skips_old_comments_but_processes_missing_posted_at(
    loop_factory,
    comment_factory,
):
    old_comment = comment_factory(
        1,
        posted_at=datetime.now() - timedelta(days=32),
    )
    missing_date_comment = comment_factory(2, posted_at=None)
    harness = loop_factory(
        comments=[old_comment, missing_date_comment],
        settings_overrides={"MAX_COMMENT_AGE_DAYS": 30},
    )

    harness.loop.run_cycle()

    assert harness.repository.comments[1].status == CommentStatus.SKIPPED
    assert harness.repository.comments[2].status == CommentStatus.ANSWERED
    assert len(harness.repository.replies) == 1
    assert next(iter(harness.repository.replies.values())).comment_id == 2
    assert len(harness.ai_provider.calls) == 1


def test_run_cycle_passes_article_text_and_marks_reply_as_article_text_used(
    loop_factory, comment_factory
):
    comment = comment_factory(1)
    comment.post_url = "https://dzen.ru/a/post"
    harness = loop_factory(comments=[comment])
    harness.page.article_text_by_url[comment.post_url] = "Article body"

    harness.loop.run_cycle()

    assert harness.prompt_builder.contexts[0].article_text == "Article body"
    assert harness.prompt_builder.contexts[0].post_url == comment.post_url
    assert next(iter(harness.repository.replies.values())).article_context_status == "article_text_used"


def test_run_cycle_falls_back_and_marks_reply_without_article_text(
    loop_factory, comment_factory
):
    comment = comment_factory(1)
    comment.post_url = "https://dzen.ru/a/post"
    harness = loop_factory(comments=[comment])
    harness.page.article_text_by_url[comment.post_url] = None

    harness.loop.run_cycle()

    assert harness.prompt_builder.contexts[0].article_text == ""
    assert next(iter(harness.repository.replies.values())).article_context_status == "without_article_text"


def test_run_cycle_skips_comment_with_published_reply(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeCommentRepository

    repository = FakeCommentRepository(published_reply_comment_ids={1})
    harness = loop_factory(
        comments=[comment_factory(1)],
        repository=repository,
    )

    harness.loop.run_cycle()

    assert harness.repository.comments[1].status == CommentStatus.SKIPPED
    assert harness.ai_provider.calls == []
    assert harness.repository.replies == {}


def test_run_cycle_skips_comment_that_repeats_own_published_reply_text(
    loop_factory,
    comment_factory,
):
    """A published reply re-appears in the next scrape as a plain comment node
    (Dzen renders it like any other reply in the thread). Without this check
    the bot would treat its own reply as a new comment and answer itself."""
    from tests.orchestrator.conftest import FakeCommentRepository

    repository = FakeCommentRepository()
    original = comment_factory(1)
    original.post_url = "https://dzen.ru/a/post1"
    parent_id = repository.upsert_comment(original)
    repository.save_reply(
        Reply(
            id=None,
            comment_id=parent_id,
            generated_text="our own reply text",
            ai_provider="fake",
            ai_model="fake",
            status=ReplyStatus.PUBLISHED,
            published_at=None,
            error_reason=None,
            created_at=datetime.now(),
        )
    )

    own_reply_echo = comment_factory(2, text="our own reply text")
    own_reply_echo.post_url = "https://dzen.ru/a/post1"

    harness = loop_factory(comments=[own_reply_echo], repository=repository)

    harness.loop.run_cycle()

    assert harness.repository.comments[2].status == CommentStatus.SKIPPED
    assert harness.ai_provider.calls == []
    assert len(harness.repository.replies) == 1


def test_safe_mode_does_not_regenerate_an_existing_reply(loop_factory, comment_factory):
    harness = loop_factory(comments=[comment_factory(1)])

    harness.loop.run_cycle()
    harness.loop.run_cycle()

    assert len(harness.ai_provider.calls) == 1
    assert len(harness.repository.replies) == 1
    assert harness.repository.comments[1].status == CommentStatus.SKIPPED


def test_run_cycle_regenerates_once_when_reply_is_too_long(
    loop_factory,
    comment_factory,
):
    harness = loop_factory(
        comments=[comment_factory(1)],
        settings_overrides={"MAX_REPLY_LENGTH": 10},
        ai_responses=["this text is too long", "short"],
    )

    harness.loop.run_cycle()

    assert len(harness.ai_provider.calls) == 2
    assert len(harness.repository.replies) == 1
    reply = next(iter(harness.repository.replies.values()))
    assert reply.generated_text == "short"
    assert reply.status == ReplyStatus.GENERATED
    assert harness.repository.comments[1].status == CommentStatus.ANSWERED


def test_run_cycle_marks_reply_error_when_regeneration_is_too_long(
    loop_factory,
    comment_factory,
):
    harness = loop_factory(
        comments=[comment_factory(1)],
        settings_overrides={
            "AUTO_PUBLISH": True,
            "MAX_REPLY_LENGTH": 10,
        },
        ai_responses=["this text is too long", "still too long"],
    )

    harness.loop.run_cycle()

    assert len(harness.ai_provider.calls) == 2
    reply = next(iter(harness.repository.replies.values()))
    assert reply.status == ReplyStatus.ERROR
    assert reply.error_reason == "reply too long after regeneration"
    assert harness.repository.comments[1].status == CommentStatus.ERROR
    assert harness.notifier.errors == [("reply too long after regeneration", None)]
    assert harness.page.publish_calls == []


def test_run_cycle_publishes_only_when_auto_publish_enabled(
    loop_factory,
    comment_factory,
):
    harness = loop_factory(
        comments=[comment_factory(1)],
        settings_overrides={"AUTO_PUBLISH": True},
        ai_responses=["ready to publish"],
    )

    harness.loop.run_cycle()

    assert harness.page.publish_calls == [
        (harness.repository.comments[1], "ready to publish", True)
    ]
    reply = next(iter(harness.repository.replies.values()))
    assert reply.status == ReplyStatus.PUBLISHED
    assert harness.repository.set_reply_status_calls == [
        (reply.id, ReplyStatus.PUBLISHED, None)
    ]


def test_run_cycle_marks_reply_error_when_publishing_fails(
    loop_factory,
    comment_factory,
):
    harness = loop_factory(
        comments=[comment_factory(1)],
        settings_overrides={"AUTO_PUBLISH": True},
    )

    def fail_publish(comment, text, *, auto_publish):
        raise RuntimeError("Dzen form changed")

    harness.page.publish_reply = fail_publish
    harness.loop.run_cycle()

    reply = next(iter(harness.repository.replies.values()))
    assert reply.status == ReplyStatus.ERROR
    assert reply.error_reason == "Dzen reply publication failed"
    assert harness.repository.comments[1].status == CommentStatus.ERROR
    assert len(harness.notifier.errors) == 1
    message, error = harness.notifier.errors[0]
    assert message == "Dzen reply publication failed"
    assert isinstance(error, RuntimeError)
    assert str(error) == "Dzen form changed"


def test_run_cycle_respects_max_replies_per_cycle(
    loop_factory,
    comment_factory,
):
    comments = [comment_factory(index) for index in range(1, 6)]
    harness = loop_factory(
        comments=comments,
        settings_overrides={"MAX_REPLIES_PER_CYCLE": 2},
    )

    harness.loop.run_cycle()

    assert len(harness.ai_provider.calls) == 2
    assert len(harness.repository.replies) == 2
    assert harness.repository.comments[1].status == CommentStatus.ANSWERED
    assert harness.repository.comments[2].status == CommentStatus.ANSWERED
    assert harness.repository.comments[3].status == CommentStatus.NEW
    assert harness.repository.comments[4].status == CommentStatus.NEW
    assert harness.repository.comments[5].status == CommentStatus.NEW


def test_run_cycle_asks_auth_assistant_and_exits_when_session_is_not_restored(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(logged_in=False, restore_results=[False, False])
    auth_assistant = FakeAuthAssistant(ask_ready_result=True)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_cycle()

    assert harness.session.restore_calls == 2
    assert harness.session.login_calls == 1
    assert harness.auth_assistant.ask_ready_calls == 1
    assert len(harness.notifier.errors) == 1
    assert harness.page.fetch_calls == 0
    assert harness.repository.upsert_publication_calls == []
    assert harness.repository.upsert_comment_calls == []


def test_run_cycle_resets_session_and_restarts_authorization_on_auth_command(
    loop_factory, comment_factory
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(logged_in=False, restore_results=[False])
    auth_assistant = FakeAuthAssistant(
        auth_command_result=True,
        ask_ready_result=False,
    )
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_cycle()

    assert session.reset_authentication_calls == 1
    assert auth_assistant.reset_ready_prompt_calls == 1
    assert auth_assistant.ask_ready_calls == 1


def test_run_cycle_saves_state_when_session_is_already_logged_in(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeSessionManager

    session = FakeSessionManager(logged_in=True)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
    )

    harness.loop.run_cycle()

    assert harness.session.save_state_calls == 1
    assert harness.session.restore_calls == 0
    assert harness.session.login_calls == 0
    assert harness.page.fetch_calls == 1


def test_run_cycle_saves_state_after_restore(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(logged_in=False, restore_results=[True])
    auth_assistant = FakeAuthAssistant(ask_ready_result=True)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_cycle()

    assert harness.session.restore_calls == 1
    assert harness.session.save_state_calls == 1
    assert harness.session.login_calls == 0
    assert harness.auth_assistant.ask_ready_calls == 0
    assert harness.page.fetch_calls == 1


def test_extract_reply_text_removes_structured_type_line():
    raw = "\u0442\u0438\u043f: \u0432\u043e\u0432\u043b\u0435\u043a\u0430\u044e\u0449\u0438\u0439\n\u043e\u0442\u0432\u0435\u0442: \u041a\u043e\u0440\u043e\u0442\u043a\u0438\u0439 \u043e\u0442\u0432\u0435\u0442"
    assert OrchestratorLoop._extract_reply_text(raw) == "\u041a\u043e\u0440\u043e\u0442\u043a\u0438\u0439 \u043e\u0442\u0432\u0435\u0442"


def test_extract_reply_text_skips_explicit_pass():
    raw = "\u0442\u0438\u043f: \u043f\u0440\u043e\u043f\u0443\u0441\u043a\n\u043e\u0442\u0432\u0435\u0442:"
    assert OrchestratorLoop._extract_reply_text(raw) == ""


def test_run_cycle_saves_manual_session_after_ready_confirmation(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(logged_in=False, restore_results=[False])

    class ManualAuthAssistant(FakeAuthAssistant):
        def ask_ready(self) -> bool:
            result = super().ask_ready()
            session.logged_in = True
            return result

    auth_assistant = ManualAuthAssistant(ask_ready_result=True)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_cycle()

    assert harness.session.restore_calls == 1
    assert harness.session.save_state_calls == 1
    assert harness.session.login_calls == 0
    assert harness.auth_assistant.ask_ready_calls == 1
    assert harness.page.fetch_calls == 1


def test_run_cycle_asks_ready_before_automated_login(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(
        logged_in=False,
        restore_results=[False],
        login_results=[True],
    )
    auth_assistant = FakeAuthAssistant(ask_ready_result=True)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_cycle()

    assert harness.session.restore_calls == 1
    assert harness.session.login_calls == 1
    assert harness.auth_assistant.ask_ready_calls == 1
    assert harness.page.fetch_calls == 1
    assert harness.notifier.errors == []


def test_run_cycle_stops_when_authorization_is_not_confirmed(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(logged_in=False, restore_results=[False])
    auth_assistant = FakeAuthAssistant(ask_ready_result=False)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_cycle()

    assert harness.session.restore_calls == 1
    assert harness.session.login_calls == 0
    assert harness.auth_assistant.ask_ready_calls == 1
    assert harness.notifier.errors == [("Dzen authorization was not confirmed", None)]
    assert harness.page.fetch_calls == 0


def test_run_forever_notifies_authorization_denied_only_once(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(logged_in=False, restore_results=[False, False, False])
    auth_assistant = FakeAuthAssistant(ask_ready_result=False)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_forever(max_cycles=3)

    assert auth_assistant.ask_ready_calls == 3
    assert harness.notifier.errors == [("Dzen authorization was not confirmed", None)]


def test_run_cycle_falls_back_to_manual_auth_when_automated_login_fails(
    loop_factory,
    comment_factory,
):
    from tests.orchestrator.conftest import FakeAuthAssistant, FakeSessionManager

    session = FakeSessionManager(
        logged_in=False,
        restore_results=[False, True],
        login_results=[RuntimeError("captcha")],
    )
    auth_assistant = FakeAuthAssistant(ask_ready_result=True)
    harness = loop_factory(
        comments=[comment_factory(1)],
        session=session,
        auth_assistant=auth_assistant,
    )

    harness.loop.run_cycle()

    assert harness.session.restore_calls == 2
    assert harness.session.login_calls == 1
    assert harness.auth_assistant.ask_ready_calls == 1
    assert harness.notifier.errors[0][0] == "Dzen automated login failed"
    assert harness.page.fetch_calls == 1


def test_auto_publish_change_applies_next_cycle_without_restart(
    loop_factory,
    comment_factory,
):
    # settings.AUTO_PUBLISH is False; the loop must obey the live runtime config.
    harness = loop_factory(comments=[])
    harness.runtime_config.data.settings.auto_publish = False

    harness.page.comments = [comment_factory(1)]
    harness.loop.run_cycle()

    assert harness.page.publish_calls[-1][2] is False
    reply1 = next(r for r in harness.repository.replies.values() if r.comment_id == 1)
    assert reply1.status == ReplyStatus.GENERATED

    # Flip the flag live — no restart, no reconstruction of the loop.
    harness.runtime_config.data.settings.auto_publish = True
    harness.page.comments = [comment_factory(2)]
    harness.loop.run_cycle()

    assert harness.page.publish_calls[-1][2] is True
    reply2 = next(r for r in harness.repository.replies.values() if r.comment_id == 2)
    assert reply2.status == ReplyStatus.PUBLISHED


def test_generated_reply_uses_naive_moscow_time(loop_factory, monkeypatch):
    from dzen_commenter.orchestrator import loop as loop_module

    expected = datetime(2026, 1, 2, 3, 4, 5)
    monkeypatch.setattr(loop_module, "moscow_now", lambda: expected)
    harness = loop_factory(comments=[])

    reply = harness.loop._make_reply(
        comment_id=1,
        text="answer",
        status=ReplyStatus.GENERATED,
        error_reason=None,
    )

    assert reply.created_at == expected
    assert reply.created_at.tzinfo is None


def test_max_comment_age_days_read_from_runtime_config(
    loop_factory,
    comment_factory,
):
    comment = comment_factory(1, posted_at=datetime.now() - timedelta(days=10))
    harness = loop_factory(comments=[comment])
    # settings.MAX_COMMENT_AGE_DAYS is 30; runtime config tightens it to 5.
    harness.runtime_config.data.settings.max_comment_age_days = 5

    harness.loop.run_cycle()

    assert harness.repository.comments[1].status == CommentStatus.SKIPPED
    assert harness.ai_provider.calls == []


def test_max_reply_length_read_from_runtime_config(
    loop_factory,
    comment_factory,
):
    harness = loop_factory(
        comments=[comment_factory(1)],
        ai_responses=["this text is too long", "still too long"],
    )
    # settings.MAX_REPLY_LENGTH is 1000; runtime config tightens it to 5.
    harness.runtime_config.data.settings.max_reply_length = 5

    harness.loop.run_cycle()

    reply = next(iter(harness.repository.replies.values()))
    assert reply.status == ReplyStatus.ERROR
    assert reply.error_reason == "reply too long after regeneration"


def test_cta_is_added_to_each_seventh_published_target_reply(loop_factory, comment_factory):
    comments = [comment_factory(index) for index in range(1, 15)]
    for comment in comments:
        comment.publication_title = "Ремонт квартиры"
    harness = loop_factory(
        comments=comments,
        settings_overrides={
            "AUTO_PUBLISH": True,
            "CTA_EVERY_N_COMMENTS": 7,
            "MAX_REPLIES_PER_CYCLE": 14,
        },
    )
    harness.runtime_config.data.prompt.cta_link = "https://saved.example/remont"

    harness.loop.run_cycle()

    texts = [reply.generated_text for reply in harness.repository.replies.values()]
    cta = "Рассчитать стоимость ремонта можно здесь: https://saved.example/remont"
    assert sum(cta in text for text in texts) == 2
    assert cta in texts[6]
    assert cta in texts[13]


def test_non_target_article_does_not_advance_cta_interval(loop_factory, comment_factory):
    comments = [comment_factory(index) for index in range(1, 8)]
    for comment in comments[:-1]:
        comment.publication_title = "Лучшие фильмы года"
    comments[-1].publication_title = "Дизайн кухни"
    harness = loop_factory(comments=comments, settings_overrides={"AUTO_PUBLISH": True})
    harness.runtime_config.data.prompt.cta_link = "https://saved.example/remont"

    harness.loop.run_cycle()

    assert "Рассчитать стоимость ремонта" not in harness.repository.replies[7].generated_text


def test_failed_target_publication_does_not_advance_cta_interval(loop_factory, comment_factory):
    comments = [comment_factory(index) for index in range(1, 8)]
    for comment in comments:
        comment.publication_title = "Ремонт кухни"
    harness = loop_factory(comments=comments, settings_overrides={"AUTO_PUBLISH": True})
    harness.runtime_config.data.prompt.cta_link = "https://saved.example/remont"

    def publish_reply(comment, text, *, auto_publish):
        if comment.dzen_comment_id == "comment-7":
            raise RuntimeError("publication failed")

    harness.page.publish_reply = publish_reply
    harness.loop.run_cycle()

    retry = comment_factory(8)
    retry.publication_title = "Ремонт кухни"
    harness.page.comments = [retry]
    harness.loop.run_cycle()

    assert "Рассчитать стоимость ремонта" in harness.repository.replies[8].generated_text


def test_cta_applies_in_draft_mode_without_auto_publish(loop_factory, comment_factory):
    comments = [comment_factory(index) for index in range(1, 8)]
    for comment in comments:
        comment.publication_title = "Ремонт квартиры"
    harness = loop_factory(
        comments=comments,
        settings_overrides={
            "AUTO_PUBLISH": False,
            "CTA_EVERY_N_COMMENTS": 7,
        },
    )
    harness.runtime_config.data.prompt.cta_link = "https://saved.example/remont"

    harness.loop.run_cycle()

    cta = "Рассчитать стоимость ремонта можно здесь: https://saved.example/remont"
    reply = harness.repository.replies[7]
    assert cta in reply.generated_text
    assert reply.status == ReplyStatus.GENERATED


def test_hourly_limit_leaves_next_comment_unprocessed(loop_factory, comment_factory, monkeypatch):
    from dzen_commenter.orchestrator import loop as loop_module

    now = datetime(2026, 7, 31, 12, 0, 0)
    monkeypatch.setattr(loop_module, "moscow_now", lambda: now)
    harness = loop_factory(
        comments=[comment_factory(1)],
        settings_overrides={"AUTO_PUBLISH": True, "MAX_COMMENTS_PER_HOUR": 100},
    )
    for index in range(100):
        reply = harness.loop._make_reply(
            comment_id=index + 1000,
            text="published",
            status=ReplyStatus.PUBLISHED,
            error_reason=None,
        )
        reply.published_at = now
        harness.repository.save_reply(reply)

    harness.loop.run_cycle()

    assert harness.repository.comments[1].status == CommentStatus.NEW
    assert harness.ai_provider.calls == []


def test_cta_does_not_overflow_or_attach_to_empty_reply(loop_factory, comment_factory):
    target = comment_factory(1)
    target.publication_title = "Ремонт кухни"
    harness = loop_factory(
        comments=[target],
        settings_overrides={"AUTO_PUBLISH": True, "MAX_REPLY_LENGTH": 5},
        ai_responses=["Тип: пропуск"],
    )
    harness.runtime_config.data.settings.cta_every_n_comments = 1
    harness.runtime_config.data.prompt.cta_link = "https://saved.example/remont"

    harness.loop.run_cycle()

    assert harness.repository.comments[1].status == CommentStatus.SKIPPED
    assert harness.repository.replies == {}


def test_cta_reserves_reply_length_before_generation(loop_factory, comment_factory, monkeypatch):
    from dzen_commenter.orchestrator import loop as loop_module

    now = datetime(2026, 7, 31, 12, 0, 0)
    monkeypatch.setattr(loop_module, "moscow_now", lambda: now)
    cta = "Рассчитать стоимость ремонта можно здесь: https://saved.example/remont"
    answer = "brief"
    target = comment_factory(7)
    target.publication_title = "Ремонт кухни"
    harness = loop_factory(
        comments=[target],
        settings_overrides={"AUTO_PUBLISH": True},
        ai_responses=[answer],
    )
    harness.runtime_config.data.prompt.cta_link = "https://saved.example/remont"
    harness.runtime_config.data.settings.max_reply_length = len(answer + "\n\n" + cta)
    for index in range(6):
        reply = harness.loop._make_reply(
            comment_id=index + 1000,
            text="published",
            status=ReplyStatus.PUBLISHED,
            error_reason=None,
            is_cta_candidate=True,
        )
        reply.published_at = now
        harness.repository.save_reply(reply)

    harness.loop.run_cycle()

    text = harness.repository.replies[7].generated_text
    assert text == answer + "\n\n" + cta
    assert len(text) == harness.runtime_config.data.settings.max_reply_length
    assert f"Длина ответа: не более {len(answer)} символов." in harness.ai_provider.calls[0][0]


def test_cta_is_omitted_when_its_text_cannot_fit_nonempty_reply(loop_factory, comment_factory):
    target = comment_factory(1)
    target.publication_title = "Ремонт кухни"
    harness = loop_factory(
        comments=[target],
        settings_overrides={"AUTO_PUBLISH": True, "MAX_REPLY_LENGTH": 5},
        ai_responses=["brief"],
    )
    harness.runtime_config.data.settings.cta_every_n_comments = 1
    harness.runtime_config.data.prompt.cta_link = "https://saved.example/remont"

    harness.loop.run_cycle()

    reply = harness.repository.replies[1]
    assert reply.status == ReplyStatus.PUBLISHED
    assert reply.generated_text == "brief"


def test_run_forever_uses_max_cycles_and_injected_sleep(
    loop_factory,
):
    harness = loop_factory(comments=[])

    harness.loop.run_forever(max_cycles=2)

    assert harness.page.fetch_calls == 2
    assert harness.sleep_calls == [
        harness.settings.POLL_INTERVAL,
        harness.settings.POLL_INTERVAL,
    ]
