from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from dzen_commenter.contracts.enums import (
    CommentStatus,
    GenerationFailureOutcome,
    PublicationFailureOutcome,
    ReplyStatus,
)
from dzen_commenter.contracts.models import (
    ArticleContext,
    ClaimedGeneration,
    ClaimedPublication,
    Comment,
    Publication,
    Reply,
)

ReplyType = Literal["lead", "engage"]


@dataclass
class PromptContext:
    publication_title: str
    thread_text: str
    reply_type: ReplyType
    comment_text: str = ""
    post_url: str | None = None
    article_text: str = ""


class CommentRepository(Protocol):
    def upsert_publication(self, pub: Publication) -> int:
        ...

    def upsert_comment(self, comment: Comment) -> int:
        ...

    def upsert_eligible_comment(
        self, comment: Comment, *, queued_at: datetime
    ) -> int:
        ...

    def enqueue_pending_generations(self, *, queued_at: datetime) -> int:
        ...

    def save_reply(self, reply: Reply) -> int:
        ...

    def set_comment_status(self, comment_id: int, status: CommentStatus) -> None:
        ...

    def skip_comment_if_new(self, comment_id: int) -> bool:
        ...

    def set_reply_status(
        self,
        reply_id: int,
        status: ReplyStatus,
        error_reason: str | None = None,
        published_at: datetime | None = None,
    ) -> None:
        ...

    def count_published_replies_since(self, since: datetime) -> int:
        ...

    def count_ai_attempts_since(self, since: datetime) -> int:
        ...

    def enqueue_generation(self, comment_id: int, *, queued_at: datetime) -> bool:
        ...

    def claim_next_generation(self, now: datetime) -> ClaimedGeneration | None:
        ...

    def complete_generation(
        self,
        comment_id: int,
        *,
        claim_token: str,
        text: str,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        created_at: datetime,
        is_cta_candidate: bool,
    ) -> int:
        ...

    def skip_generation(
        self,
        comment_id: int,
        *,
        claim_token: str,
        reason: str,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        created_at: datetime,
    ) -> int:
        ...

    def fail_generation(
        self,
        comment_id: int,
        *,
        claim_token: str,
        error_reason: str,
        failed_at: datetime,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        retry_cooldown_minutes: int,
        max_attempts_per_comment: int,
    ) -> GenerationFailureOutcome:
        ...

    def get_article_context(self, publication_id: int) -> ArticleContext | None:
        ...

    def save_article_context(
        self,
        publication_id: int,
        *,
        text: str | None,
        status: str,
        fetched_at: datetime,
    ) -> ArticleContext:
        ...

    def enqueue_publication(self, reply_id: int, *, created_at: datetime) -> bool:
        ...

    def expire_stale_publications(
        self,
        now: datetime,
        oldest_allowed_comment_fetched_at: datetime,
    ) -> int:
        ...

    def claim_next_publication(self, now: datetime) -> ClaimedPublication | None:
        ...

    def complete_publication(
        self,
        reply_id: int,
        *,
        claim_token: str,
        published_at: datetime | None,
    ) -> None:
        ...

    def fail_publication(
        self,
        reply_id: int,
        *,
        claim_token: str,
        error_reason: str,
        failed_at: datetime,
        retry_cooldown_minutes: int,
        max_attempts_per_reply: int,
    ) -> PublicationFailureOutcome:
        ...

    def count_cta_candidates_produced(self) -> int:
        ...

    def has_generated_reply(self, comment_id: int) -> bool:
        ...

    def has_published_reply(self, comment_id: int) -> bool:
        ...

    def is_own_reply(self, post_url: str | None, text: str) -> bool:
        ...


class AIProvider(Protocol):
    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        ...


class PromptBuilder(Protocol):
    def build(self, context: PromptContext) -> str:
        ...


class SessionManager(Protocol):
    def start(self) -> None:
        ...

    def browser_access(self) -> AbstractContextManager[None]:
        ...

    def is_logged_in(self) -> bool:
        ...

    def login(self) -> bool:
        ...

    def save_state(self) -> None:
        ...

    def restore(self) -> bool:
        ...

    def reset_authentication(self) -> None:
        ...


class DzenPage(Protocol):
    def fetch_comments(self) -> list[Comment]:
        ...

    def fetch_article_text(self, post_url: str) -> str | None:
        ...

    def publish_reply(self, comment: Comment, text: str, *, auto_publish: bool) -> None:
        ...


class Notifier(Protocol):
    def notify(self, message: str) -> None:
        ...

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        ...


class AuthAssistant(Protocol):
    def poll_auth_command(self) -> bool:
        ...

    def reset_ready_prompt(self) -> None:
        ...

    def ask_ready(self) -> bool:
        ...

    def notify_sms_restart(self) -> None:
        ...

    def notify_sms_pending(self) -> None:
        ...

    def relay_code_prompt(self, prompt_text: str) -> str:
        ...
