from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from dzen_commenter.config.runtime_config import RuntimeConfigData, RuntimeSettings
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.enums import BatchOutcomeKind, CommentStatus, ReplyStatus
from dzen_commenter.contracts.interfaces import PromptContext, ReplyType
from dzen_commenter.contracts.models import (
    BatchItem,
    BatchOutcome,
    ClaimedBatch,
    Comment,
    Publication,
    Reply,
)
from dzen_commenter.orchestrator import OrchestratorLoop
from dzen_commenter.prompt import parse_batch
from dzen_commenter.prompt.config_loader import load_brand_config

_MISSING = object()


class FakeRuntimeConfig:
    """In-memory RuntimeConfig stand-in whose settings can be mutated live."""

    def __init__(self, data: RuntimeConfigData) -> None:
        self.data = data
        self.get_calls = 0

    def get(self) -> RuntimeConfigData:
        self.get_calls += 1
        return self.data


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
        self.batch_queue: dict[int, dict[str, object]] = {}
        self.claimed_batches: dict[int, ClaimedBatch] = {}
        self.save_batch_outcomes_calls: list[tuple[int, tuple[BatchOutcome, ...]]] = []
        self._next_publication_id = 1
        self._next_comment_id = 1
        self._next_reply_id = 1
        self._next_batch_id = 1

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
        published_at: datetime | None = None,
    ) -> None:
        self.set_reply_status_calls.append((reply_id, status, error_reason))
        reply = self.replies[reply_id]
        reply.status = status
        reply.error_reason = error_reason
        if published_at is not None:
            reply.published_at = published_at
        if status == ReplyStatus.PUBLISHED:
            self.published_reply_comment_ids.add(reply.comment_id)

    def count_published_replies_since(self, since: datetime) -> int:
        return sum(
            reply.status == ReplyStatus.PUBLISHED
            and reply.published_at is not None
            and reply.published_at >= since
            for reply in self.replies.values()
        )

    def count_ai_attempts_since(self, since: datetime) -> int:
        return sum(
            reply.status
            in (
                ReplyStatus.GENERATED,
                ReplyStatus.PUBLISHED,
                ReplyStatus.ERROR,
                ReplyStatus.SKIPPED,
            )
            and reply.created_at is not None
            and reply.created_at >= since
            for reply in self.replies.values()
        )

    def count_cta_candidates_produced(self) -> int:
        return sum(
            reply.status in (ReplyStatus.GENERATED, ReplyStatus.PUBLISHED)
            and reply.is_cta_candidate
            for reply in self.replies.values()
        )

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

    def is_own_reply(self, post_url: str | None, text: str) -> bool:
        if not post_url:
            return False
        return any(
            reply.status == ReplyStatus.PUBLISHED
            and reply.generated_text == text
            and self.comments.get(reply.comment_id) is not None
            and self.comments[reply.comment_id].post_url == post_url
            for reply in self.replies.values()
        )

    def enqueue_batch_comment(
        self,
        comment_id: int,
        post_url: str,
        *,
        queued_at: datetime,
        cutover_at: datetime,
    ) -> bool:
        comment = self.comments[comment_id]
        if (
            comment.status is not CommentStatus.NEW
            or comment.post_url != post_url
            or comment.fetched_at is None
            or comment.fetched_at < cutover_at
            or self.has_generated_reply(comment_id)
            or comment_id in self.batch_queue
        ):
            return False
        self.batch_queue[comment_id] = {
            "post_url": post_url,
            "queued_at": queued_at,
            "state": "queued",
            "attempt_count": 0,
            "claimed_batch_id": None,
            "next_attempt_at": None,
        }
        return True

    def claim_next_batch(
        self,
        now: datetime,
        *,
        max_comments: int,
        wait_hours: int,
        quota_remaining: int,
    ) -> ClaimedBatch | None:
        limit = min(max_comments, quota_remaining)
        if limit <= 0:
            return None
        queued = [
            (comment_id, row)
            for comment_id, row in self.batch_queue.items()
            if row["state"] == "queued"
            and (row["next_attempt_at"] is None or row["next_attempt_at"] <= now)
        ]
        if not queued:
            return None
        queued.sort(key=lambda entry: (entry[1]["queued_at"], entry[0]))
        post_url = str(queued[0][1]["post_url"])
        selected = [entry for entry in queued if entry[1]["post_url"] == post_url][
            :limit
        ]
        if len(selected) < limit and selected[0][1]["queued_at"] > now - timedelta(
            hours=wait_hours
        ):
            return None
        batch_id = self._next_batch_id
        self._next_batch_id += 1
        items = tuple(
            BatchItem(
                batch_id=batch_id,
                comment_id=comment_id,
                item_no=item_no,
                post_url=post_url,
                publication_title=self.comments[comment_id].publication_title,
                thread_text=self.comments[comment_id].thread_text,
                author=self.comments[comment_id].author,
                comment_text=self.comments[comment_id].text,
            )
            for item_no, (comment_id, _) in enumerate(selected, start=1)
        )
        batch = ClaimedBatch(batch_id, post_url, now, items)
        self.claimed_batches[batch_id] = batch
        for comment_id, row in selected:
            row["state"] = "claimed"
            row["claimed_batch_id"] = batch_id
            row["attempt_count"] = int(row["attempt_count"]) + 1
        return batch

    def save_batch_outcomes(
        self,
        batch_id: int,
        outcomes: tuple[BatchOutcome, ...],
        *,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        created_at: datetime,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        retry_cooldown_minutes: int,
        max_attempts_per_comment: int,
    ) -> tuple[int, ...]:
        batch = self.claimed_batches[batch_id]
        expected = [(item.comment_id, item.item_no) for item in batch.items]
        actual = [(outcome.comment_id, outcome.item_no) for outcome in outcomes]
        if actual != expected:
            raise ValueError("Batch outcomes do not match the claimed item order")
        self.save_batch_outcomes_calls.append((batch_id, outcomes))
        reply_ids: list[int] = []
        for outcome in outcomes:
            status = {
                BatchOutcomeKind.REPLY: ReplyStatus.GENERATED,
                BatchOutcomeKind.SKIP: ReplyStatus.SKIPPED,
                BatchOutcomeKind.ERROR: ReplyStatus.ERROR,
            }[outcome.kind]
            reply_id = self.save_reply(
                Reply(
                    id=None,
                    comment_id=outcome.comment_id,
                    generated_text=outcome.text,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    status=status,
                    published_at=None,
                    error_reason=outcome.error_reason,
                    created_at=created_at,
                    article_context_status=article_context_status,
                )
            )
            reply_ids.append(reply_id)
            self.comments[outcome.comment_id].status = {
                BatchOutcomeKind.REPLY: CommentStatus.ANSWERED,
                BatchOutcomeKind.SKIP: CommentStatus.SKIPPED,
                BatchOutcomeKind.ERROR: CommentStatus.ERROR,
            }[outcome.kind]
            queue = self.batch_queue[outcome.comment_id]
            retry = (
                outcome.kind is BatchOutcomeKind.ERROR
                and int(queue["attempt_count"]) < max_attempts_per_comment
            )
            queue["state"] = "queued" if retry else "completed"
            queue["claimed_batch_id"] = None if retry else batch_id
            queue["next_attempt_at"] = (
                created_at + timedelta(minutes=retry_cooldown_minutes)
                if retry
                else None
            )
        return tuple(reply_ids)


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


class FakeBatchPromptBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[BatchItem, ...], str]] = []

    def build_batch(self, items, *, article_text: str) -> str:
        item_tuple = tuple(items)
        self.calls.append((item_tuple, article_text))
        return "batch prompt"


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
        self.reset_authentication_calls = 0

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

    def reset_authentication(self) -> None:
        self.reset_authentication_calls += 1
        self.logged_in = False


class FakeDzenPage:
    def __init__(self, comments: list[Comment] | None = None) -> None:
        self.comments = list(comments or [])
        self.fetch_calls = 0
        self.publish_calls: list[tuple[Comment, str, bool]] = []
        self.article_text_by_url: dict[str, str | None] = {}
        self.article_text_urls: list[str] = []

    def fetch_comments(self) -> list[Comment]:
        self.fetch_calls += 1
        return list(self.comments)

    def fetch_article_text(self, post_url: str) -> str | None:
        self.article_text_urls.append(post_url)
        return self.article_text_by_url.get(post_url)

    def publish_reply(self, comment: Comment, text: str, *, auto_publish: bool) -> None:
        self.publish_calls.append((comment, text, auto_publish))


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[tuple[str, Exception | None]] = []

    def notify(self, message: str) -> None:
        self.messages.append(message)

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        self.errors.append((message, error))


class FakeAuthAssistant:
    def __init__(
        self,
        *,
        ask_ready_result: bool = True,
        auth_command_result: bool = False,
    ) -> None:
        self.ask_ready_result = ask_ready_result
        self.auth_command_result = auth_command_result
        self.ask_ready_calls = 0
        self.poll_auth_command_calls = 0
        self.reset_ready_prompt_calls = 0
        self.relay_code_prompt_calls: list[str] = []
        self.sms_restart_notifications = 0

    def ask_ready(self) -> bool:
        self.ask_ready_calls += 1
        return self.ask_ready_result

    def poll_auth_command(self) -> bool:
        self.poll_auth_command_calls += 1
        return self.auth_command_result

    def reset_ready_prompt(self) -> None:
        self.reset_ready_prompt_calls += 1

    def notify_sms_restart(self) -> None:
        self.sms_restart_notifications += 1

    def notify_sms_pending(self) -> None:
        pass

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


class FakeCtaCandidateClassifier:
    def __call__(self, publication_title: str) -> bool:
        return any(
            keyword in publication_title.lower()
            for keyword in ("ремонт", "дизайн", "интерьер", "отделк", "планировк")
        )


@dataclass
class LoopHarness:
    loop: OrchestratorLoop
    settings: Settings
    repository: FakeCommentRepository
    ai_provider: FakeAIProvider
    prompt_builder: FakePromptBuilder
    batch_prompt_builder: FakeBatchPromptBuilder
    session: FakeSessionManager
    page: FakeDzenPage
    notifier: FakeNotifier
    auth_assistant: FakeAuthAssistant
    classify_reply_type: FakeReplyClassifier
    is_cta_candidate_title: FakeCtaCandidateClassifier
    runtime_config: FakeRuntimeConfig
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
        moved_fields = {
            "AUTO_PUBLISH",
            "MAX_COMMENT_AGE_DAYS",
            "MAX_REPLY_LENGTH",
            "DEVELOPER_TELEGRAM_CHAT_ID_LIST",
            "EMAIL_FALLBACK_LIST",
            "PROMPT_CONFIG_PATH",
        }
        values.update(
            {key: value for key, value in overrides.items() if key not in moved_fields}
        )
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
        runtime_overrides = settings_overrides or {}
        settings = settings_factory(**runtime_overrides)
        repository = repository or FakeCommentRepository()
        ai_provider = FakeAIProvider(ai_responses)
        prompt_builder = FakePromptBuilder()
        batch_prompt_builder = FakeBatchPromptBuilder()
        session = session or FakeSessionManager()
        page = FakeDzenPage(comments)
        notifier = FakeNotifier()
        auth_assistant = auth_assistant or FakeAuthAssistant()
        classifier = classifier or FakeReplyClassifier()
        cta_candidate_classifier = FakeCtaCandidateClassifier()
        sleep_calls: list[float] = []

        runtime_config = FakeRuntimeConfig(
            RuntimeConfigData(
                settings=RuntimeSettings(
                    auto_publish=runtime_overrides.get("AUTO_PUBLISH", False),
                    max_comment_age_days=runtime_overrides.get(
                        "MAX_COMMENT_AGE_DAYS", 30
                    ),
                    max_reply_length=runtime_overrides.get("MAX_REPLY_LENGTH", 1000),
                    cta_every_n_comments=runtime_overrides.get(
                        "CTA_EVERY_N_COMMENTS", 7
                    ),
                    max_comments_per_hour=runtime_overrides.get(
                        "MAX_COMMENTS_PER_HOUR", 100
                    ),
                    developer_telegram_chat_ids=runtime_overrides.get(
                        "DEVELOPER_TELEGRAM_CHAT_ID_LIST", ""
                    ),
                    error_email_list=runtime_overrides.get("EMAIL_FALLBACK_LIST", ""),
                    batch_replies_enabled=runtime_overrides.get(
                        "BATCH_REPLIES_ENABLED", False
                    ),
                    batch_cutover_at=runtime_overrides.get("BATCH_CUTOVER_AT"),
                    batch_max_comments=runtime_overrides.get("BATCH_MAX_COMMENTS", 3),
                    batch_wait_hours=runtime_overrides.get("BATCH_WAIT_HOURS", 12),
                    batch_retry_cooldown_minutes=runtime_overrides.get(
                        "BATCH_RETRY_COOLDOWN_MINUTES", 60
                    ),
                    batch_max_attempts_per_comment=runtime_overrides.get(
                        "BATCH_MAX_ATTEMPTS_PER_COMMENT", 2
                    ),
                ),
                prompt=load_brand_config(None),
            )
        )

        loop = OrchestratorLoop(
            settings=settings,
            repository=repository,
            ai_provider=ai_provider,
            prompt_builder=prompt_builder,
            batch_prompt_builder=batch_prompt_builder,
            batch_reply_parser=parse_batch,
            session=session,
            page=page,
            notifier=notifier,
            auth_assistant=auth_assistant,
            classify_reply_type=classifier,
            is_cta_candidate_title=cta_candidate_classifier,
            runtime_config=runtime_config,
            sleep_fn=sleep_calls.append,
        )

        return LoopHarness(
            loop=loop,
            settings=settings,
            repository=repository,
            ai_provider=ai_provider,
            prompt_builder=prompt_builder,
            batch_prompt_builder=batch_prompt_builder,
            session=session,
            page=page,
            notifier=notifier,
            auth_assistant=auth_assistant,
            classify_reply_type=classifier,
            is_cta_candidate_title=cta_candidate_classifier,
            runtime_config=runtime_config,
            sleep_calls=sleep_calls,
        )

    return _factory
