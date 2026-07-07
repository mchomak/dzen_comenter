from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.interfaces import (
    AIProvider,
    AuthAssistant,
    CommentRepository,
    DzenPage,
    Notifier,
    PromptBuilder,
    PromptContext,
    ReplyType,
    SessionManager,
)
from dzen_commenter.contracts.models import Comment, Publication, Reply


class OrchestratorLoop:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: CommentRepository,
        ai_provider: AIProvider,
        prompt_builder: PromptBuilder,
        session: SessionManager,
        page: DzenPage,
        notifier: Notifier,
        auth_assistant: AuthAssistant,
        classify_reply_type: Callable[[str, str], ReplyType],
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.ai_provider = ai_provider
        self.prompt_builder = prompt_builder
        self.session = session
        self.page = page
        self.notifier = notifier
        self.auth_assistant = auth_assistant
        self.classify_reply_type = classify_reply_type
        self.sleep_fn = sleep_fn

    def run_cycle(self) -> None:
        if not self._ensure_session():
            return

        publication_id = self.repository.upsert_publication(
            Publication(
                id=None,
                dzen_publication_id=self.settings.COMMENTS_URL,
                title=self.settings.COMMENTS_URL,
                url=self.settings.COMMENTS_URL,
            )
        )

        comments = self.page.fetch_comments()
        indexed_comments: list[tuple[int, Comment]] = []
        for comment in comments:
            comment.publication_id = publication_id
            comment_id = self.repository.upsert_comment(comment)
            comment.id = comment_id
            indexed_comments.append((comment_id, comment))

        generated_replies = 0
        for comment_id, comment in indexed_comments:
            if generated_replies >= self.settings.MAX_REPLIES_PER_CYCLE:
                break

            if self.repository.has_published_reply(comment_id):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue

            if self._is_too_old(comment.posted_at):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue

            self._generate_reply(comment_id, comment)
            generated_replies += 1

    def run_forever(self, *, max_cycles: int | None = None) -> None:
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            self.run_cycle()
            self.sleep_fn(self.settings.POLL_INTERVAL)
            cycles += 1

    def _ensure_session(self) -> bool:
        if self.session.is_logged_in():
            return True

        restored = self.session.restore()
        if not restored and self.auth_assistant.ask_ready():
            restored = self.session.restore()

        if restored:
            return True

        self.notifier.notify_error("Dzen session is not restored")
        return False

    def _is_too_old(self, posted_at: datetime | None) -> bool:
        if posted_at is None:
            return False

        if posted_at.tzinfo is None or posted_at.tzinfo.utcoffset(posted_at) is None:
            now = datetime.now()
        else:
            now = datetime.now(posted_at.tzinfo)

        return (now - posted_at).days > self.settings.MAX_COMMENT_AGE_DAYS

    def _generate_reply(self, comment_id: int, comment: Comment) -> None:
        reply_type = self.classify_reply_type(
            publication_title=self.settings.COMMENTS_URL,
            thread_text=comment.text,
        )
        prompt = self.prompt_builder.build(
            PromptContext(
                publication_title=self.settings.COMMENTS_URL,
                thread_text=comment.text,
                reply_type=reply_type,
            )
        )
        text = self.ai_provider.generate(
            prompt,
            temperature=self.settings.AI_TEMPERATURE,
            max_tokens=self.settings.AI_MAX_TOKENS,
        )

        if len(text) > self.settings.MAX_REPLY_LENGTH:
            text = self.ai_provider.generate(
                prompt,
                temperature=self.settings.AI_TEMPERATURE,
                max_tokens=self.settings.AI_MAX_TOKENS,
            )

        if len(text) > self.settings.MAX_REPLY_LENGTH:
            reason = "reply too long after regeneration"
            self.repository.save_reply(
                self._make_reply(
                    comment_id=comment_id,
                    text=text,
                    status=ReplyStatus.ERROR,
                    error_reason=reason,
                )
            )
            self.repository.set_comment_status(comment_id, CommentStatus.ERROR)
            self.notifier.notify_error(reason)
            return

        reply_id = self.repository.save_reply(
            self._make_reply(
                comment_id=comment_id,
                text=text,
                status=ReplyStatus.GENERATED,
                error_reason=None,
            )
        )
        self.repository.set_comment_status(comment_id, CommentStatus.ANSWERED)

        if self.settings.AUTO_PUBLISH:
            self.page.publish_reply(comment, text)
            self.repository.set_reply_status(reply_id, ReplyStatus.PUBLISHED)

    def _make_reply(
        self,
        *,
        comment_id: int,
        text: str,
        status: ReplyStatus,
        error_reason: str | None,
    ) -> Reply:
        return Reply(
            id=None,
            comment_id=comment_id,
            generated_text=text,
            ai_provider=self.settings.AI_PROVIDER,
            ai_model=self.settings.AI_MODEL,
            status=status,
            published_at=None,
            error_reason=error_reason,
            created_at=datetime.now(),
        )
