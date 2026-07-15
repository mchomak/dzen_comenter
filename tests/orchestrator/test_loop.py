import ast
import inspect
import pathlib
from datetime import datetime, timedelta

from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
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


def test_run_cycle_generates_replies_without_auto_publish(
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
    assert harness.page.publish_calls == []
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
        (harness.repository.comments[1], "ready to publish")
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

    def fail_publish(comment, text):
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
