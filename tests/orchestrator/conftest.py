from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pytest

from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.interfaces import PromptContext, ReplyType
from dzen_commenter.contracts.models import Comment, Publication, Reply
from dzen_commenter.orchestrator import OrchestratorLoop


_MISSING = object()


class FakeCommentRepository:
    def __init__(
        self,
        *,
        published_reply_comment_ids: set[int] | None = None,
    ) -> None:
        self.publications: dict[int, Publication] = {}
        self.publication_ids_by_dzen_id: dict[str, int] = {}
        self.comments: dict[int, Comment] = {}
        self.comment_ids_by_dzen_id: dict[str, int] = {}
        self.replies: dict[int, Reply] = {}
        self.published_reply_comment_ids = set(published_reply_comment_ids or set())
        self.upsert_publication_calls: list[Publication] = []
        self.upsert_comment_calls: list[Comment] = []
        self.set_comment_status_calls: list[tuple[int, CommentStatus]] = []
        self.set_reply_status_calls: list[tuple[int, ReplyStatus, str | None]] = []
        self.has_generated_reply_calls: list[int] = []
        self.has_published_reply_calls: list[int] = []
        self._next_publication_id = 1
        self._next_comment_id = 1
        self._next_reply_id = 1

    def upsert_publication(self, pub: Publication) -> int:
        self.upsert_publication_calls.append(pub)
        existing_id = self.publication_ids_by_dzen_id.get(pub.dzen_publication_id)
        if existing_id is not None:
            return existing_id

        publication_id = self._next_publication_id
        self._next_publication_id += 1
        pub.id = publication_id
        self.publications[publication_id] = pub
        self.publication_ids_by_dzen_id[pub.dzen_publication_id] = publication_id
        return publication_id

    def upsert_comment(self, comment: Comment) -> int:
        self.upsert_comment_calls.append(comment)
        existing_id = self.comment_ids_by_dzen_id.get(comment.dzen_comment_id)
        if existing_id is not None:
            comment.id = existing_id
            self.comments[existing_id] = comment
            return existing_id

        comment_id = self._next_comment_id
        self._next_comment_id += 1
        comment.id = comment_id
        self.comments[comment_id] = comment
        self.comment_ids_by_dzen_id[comment.dzen_comment_id] = comment_id
        return comment_id

    def save_reply(self, reply: Reply) -> int:
        reply_id = self._next_reply_id
        self._next_reply_id += 1
        reply.id = reply_id
        self.replies[reply_id] = reply
        if reply.status == ReplyStatus.PUBLISHED:
            self.published_reply_comment_ids.add(reply.comment_id)
        return reply_id

    def set_comment_status(self, comment_id: int, status: CommentStatus) -> None:
        self.set_comment_status_calls.append((comment_id, status))
        self.comments[comment_id].status = status

    def set_reply_status(
        self,
        reply_id: int,
        status: ReplyStatus,
        error_reason: str | None = None,
    ) -> None:
        self.set_reply_status_calls.append((reply_id, status, error_reason))
        reply = self.replies[reply_id]
        reply.status = status
        reply.error_reason = error_reason
        if status == ReplyStatus.PUBLISHED:
            self.published_reply_comment_ids.add(reply.comment_id)

    def has_published_reply(self, comment_id: int) -> bool:
        self.has_published_reply_calls.append(comment_id)
        return comment_id in self.published_reply_comment_ids

    def has_generated_reply(self, comment_id: int) -> bool:
        self.has_generated_reply_calls.append(comment_id)
        return comment_id in self.published_reply_comment_ids or any(
            reply.comment_id == comment_id
            and reply.status in (ReplyStatus.GENERATED, ReplyStatus.PUBLISHED)
            for reply in self.replies.values()
        )


class FakeAIProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, float, int]] = []
        self.default_response = "generated reply"

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        self.calls.append((prompt, temperature, max_tokens))
        if self.responses:
            return self.responses.pop(0)
        return self.default_response


class FakePromptBuilder:
    def __init__(self) -> None:
        self.contexts: list[PromptContext] = []

    def build(self, context: PromptContext) -> str:
        self.contexts.append(context)
        return f"prompt:{context.reply_type}:{context.thread_text}"


class FakeSessionManager:
    def __init__(
        self,
        *,
        logged_in: bool = True,
        restore_results: list[bool] | None = None,
        login_results: list[bool | Exception] | None = None,
    ) -> None:
        self.logged_in = logged_in
        self.restore_results = list(restore_results or [])
        self.login_results = list(login_results or [])
        self.start_calls = 0
        self.is_logged_in_calls = 0
        self.restore_calls = 0
        self.login_calls = 0
        self.save_state_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def is_logged_in(self) -> bool:
        self.is_logged_in_calls += 1
        return self.logged_in

    def save_state(self) -> None:
        self.save_state_calls += 1

    def login(self) -> bool:
        self.login_calls += 1
        if self.login_results:
            result = self.login_results.pop(0)
            if isinstance(result, Exception):
                raise result
        else:
            result = self.logged_in
        self.logged_in = result
        return result

    def restore(self) -> bool:
        self.restore_calls += 1
        if self.restore_results:
            result = self.restore_results.pop(0)
        else:
            result = self.logged_in
        self.logged_in = result
        return result


class FakeDzenPage:
    def __init__(self, comments: list[Comment] | None = None) -> None:
        self.comments = list(comments or [])
        self.fetch_calls = 0
        self.publish_calls: list[tuple[Comment, str]] = []

    def fetch_comments(self) -> list[Comment]:
        self.fetch_calls += 1
        return list(self.comments)

    def publish_reply(self, comment: Comment, text: str) -> None:
        self.publish_calls.append((comment, text))


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[tuple[str, Exception | None]] = []

    def notify(self, message: str) -> None:
        self.messages.append(message)

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        self.errors.append((message, error))


class FakeAuthAssistant:
    def __init__(self, *, ask_ready_result: bool = True) -> None:
        self.ask_ready_result = ask_ready_result
        self.ask_ready_calls = 0
        self.relay_code_prompt_calls: list[str] = []

    def ask_ready(self) -> bool:
        self.ask_ready_calls += 1
        return self.ask_ready_result

    def relay_code_prompt(self, prompt_text: str) -> str:
        self.relay_code_prompt_calls.append(prompt_text)
        return "000000"


class FakeReplyClassifier:
    def __init__(self, reply_type: ReplyType = "engage") -> None:
        self.reply_type = reply_type
        self.calls: list[tuple[str, str]] = []

    def __call__(self, publication_title: str, thread_text: str) -> ReplyType:
        self.calls.append((publication_title, thread_text))
        return self.reply_type


@dataclass
class LoopHarness:
    loop: OrchestratorLoop
    settings: Settings
    repository: FakeCommentRepository
    ai_provider: FakeAIProvider
    prompt_builder: FakePromptBuilder
    session: FakeSessionManager
    page: FakeDzenPage
    notifier: FakeNotifier
    auth_assistant: FakeAuthAssistant
    classify_reply_type: FakeReplyClassifier
    sleep_calls: list[float]


def make_comment(
    index: int,
    *,
    text: str | None = None,
    posted_at: datetime | None | object = _MISSING,
) -> Comment:
    if posted_at is _MISSING:
        posted_at = datetime.now()

    return Comment(
        id=None,
        dzen_comment_id=f"comment-{index}",
        publication_id=0,
        author=f"author-{index}",
        text=text or f"comment text {index}",
        parent_comment_id=None,
        posted_at=posted_at,
        fetched_at=datetime.now(),
        status=CommentStatus.NEW,
    )


@pytest.fixture
def comment_factory() -> Callable[..., Comment]:
    return make_comment


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def _factory(**overrides: object) -> Settings:
        values = {
            "DATABASE_URL": "postgresql://user:pass@localhost:5432/dzen",
            "AI_PROVIDER": "fake-ai",
            "AI_MODEL": "fake-model",
            "AI_API_KEY": "fake-key",
            "AI_BASE_URL": "https://ai.example.test",
            "AI_TEMPERATURE": 0.2,
            "AI_MAX_TOKENS": 128,
            "AI_PROMPT_LANGUAGE": "ru",
            "USER_DATA_DIR": ".user-data",
            "STORAGE_STATE_PATH": ".state.json",
            "HEADLESS": True,
            "COMMENTS_URL": "https://dzen.example.test/comments",
            "POLL_INTERVAL": 15,
            "KEEPALIVE_INTERVAL": 60,
            "AUTO_PUBLISH": False,
            "MAX_REPLIES_PER_CYCLE": 10,
            "MAX_COMMENT_AGE_DAYS": 30,
            "MAX_REPLY_LENGTH": 1000,
        }
        values.update(overrides)
        return Settings(**values)

    return _factory


@pytest.fixture
def loop_factory(
    settings_factory: Callable[..., Settings],
) -> Callable[..., LoopHarness]:
    def _factory(
        *,
        comments: list[Comment] | None = None,
        settings_overrides: dict[str, object] | None = None,
        ai_responses: list[str] | None = None,
        repository: FakeCommentRepository | None = None,
        session: FakeSessionManager | None = None,
        auth_assistant: FakeAuthAssistant | None = None,
        classifier: FakeReplyClassifier | None = None,
    ) -> LoopHarness:
        settings = settings_factory(**(settings_overrides or {}))
        repository = repository or FakeCommentRepository()
        ai_provider = FakeAIProvider(ai_responses)
        prompt_builder = FakePromptBuilder()
        session = session or FakeSessionManager()
        page = FakeDzenPage(comments)
        notifier = FakeNotifier()
        auth_assistant = auth_assistant or FakeAuthAssistant()
        classifier = classifier or FakeReplyClassifier()
        sleep_calls: list[float] = []

        loop = OrchestratorLoop(
            settings=settings,
            repository=repository,
            ai_provider=ai_provider,
            prompt_builder=prompt_builder,
            session=session,
            page=page,
            notifier=notifier,
            auth_assistant=auth_assistant,
            classify_reply_type=classifier,
            sleep_fn=sleep_calls.append,
        )

        return LoopHarness(
            loop=loop,
            settings=settings,
            repository=repository,
            ai_provider=ai_provider,
            prompt_builder=prompt_builder,
            session=session,
            page=page,
            notifier=notifier,
            auth_assistant=auth_assistant,
            classify_reply_type=classifier,
            sleep_calls=sleep_calls,
        )

    return _factory
