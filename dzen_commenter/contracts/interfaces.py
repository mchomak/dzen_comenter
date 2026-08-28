from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.models import (
    BatchItem,
    BatchOutcome,
    ClaimedBatch,
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
    def upsert_publication(self, pub: Publication) -> int: ...
    def upsert_comment(self, comment: Comment) -> int: ...
    def save_reply(self, reply: Reply) -> int: ...
    def set_comment_status(self, comment_id: int, status: CommentStatus) -> None: ...
    def set_reply_status(
        self,
        reply_id: int,
        status: ReplyStatus,
        error_reason: str | None = None,
        published_at: datetime | None = None,
    ) -> None: ...
    def count_published_replies_since(self, since: datetime) -> int: ...
    def count_ai_attempts_since(self, since: datetime) -> int: ...
    def enqueue_batch_comment(
        self,
        comment_id: int,
        post_url: str,
        *,
        queued_at: datetime,
        cutover_at: datetime,
    ) -> bool: ...
    def claim_next_batch(
        self,
        now: datetime,
        *,
        max_comments: int,
        wait_hours: int,
        quota_remaining: int,
    ) -> ClaimedBatch | None: ...
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
    ) -> tuple[int, ...]: ...
    def count_cta_candidates_produced(self) -> int: ...
    def has_generated_reply(self, comment_id: int) -> bool: ...
    def has_published_reply(self, comment_id: int) -> bool: ...
    def is_own_reply(self, post_url: str | None, text: str) -> bool: ...


class AIProvider(Protocol):
    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str: ...


class PromptBuilder(Protocol):
    def build(self, context: PromptContext) -> str: ...


class BatchPromptBuilder(Protocol):
    def build_batch(self, items: Sequence[BatchItem], *, article_text: str) -> str: ...


class SessionManager(Protocol):
    def start(self) -> None: ...
    def browser_access(self) -> AbstractContextManager[None]: ...
    def is_logged_in(self) -> bool: ...
    def login(self) -> bool: ...
    def save_state(self) -> None: ...
    def restore(self) -> bool: ...
    def reset_authentication(self) -> None: ...


class DzenPage(Protocol):
    def fetch_comments(self) -> list[Comment]: ...
    def fetch_article_text(self, post_url: str) -> str | None: ...
    def publish_reply(
        self, comment: Comment, text: str, *, auto_publish: bool
    ) -> None: ...


class Notifier(Protocol):
    def notify(self, message: str) -> None: ...
    def notify_error(self, message: str, error: Exception | None = None) -> None: ...


class AuthAssistant(Protocol):
    def poll_auth_command(self) -> bool: ...
    def reset_ready_prompt(self) -> None: ...
    def ask_ready(self) -> bool: ...
    def notify_sms_restart(self) -> None: ...
    def notify_sms_pending(self) -> None: ...
    def relay_code_prompt(self, prompt_text: str) -> str: ...
