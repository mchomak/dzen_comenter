from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from dzen_commenter.config.runtime_config import RuntimeConfig
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
        runtime_config: RuntimeConfig,
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
        self.runtime_config = runtime_config
        self.sleep_fn = sleep_fn
        self._authorization_not_confirmed_notified = False

    def run_cycle(self) -> None:
        if self.auth_assistant.poll_auth_command():
            self.session.reset_authentication()
            self.auth_assistant.reset_ready_prompt()

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

            if self.repository.has_generated_reply(comment_id):
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
        if self._save_current_session_if_logged_in():
            return self._session_ready()

        if self._restore_saved_session():
            return self._session_ready()

        if not self.auth_assistant.ask_ready():
            if not self._authorization_not_confirmed_notified:
                self.notifier.notify_error("Dzen authorization was not confirmed")
                self._authorization_not_confirmed_notified = True
            return False

        if self._save_current_session_if_logged_in():
            return self._session_ready()

        try:
            if self.session.login():
                return self._session_ready()
        except Exception as exc:
            self.notifier.notify_error("Dzen automated login failed", exc)

        if self._save_current_session_if_logged_in():
            return self._session_ready()

        if self._restore_saved_session():
            return self._session_ready()

        self.notifier.notify_error("Dzen session is not restored")
        return False

    def _session_ready(self) -> bool:
        self._authorization_not_confirmed_notified = False
        return True

    def _save_current_session_if_logged_in(self) -> bool:
        if not self.session.is_logged_in():
            return False
        self.session.save_state()
        return True

    def _restore_saved_session(self) -> bool:
        if not self.session.restore():
            return False
        self.session.save_state()
        return True

    def _is_too_old(self, posted_at: datetime | None) -> bool:
        if posted_at is None:
            return False

        if posted_at.tzinfo is None or posted_at.tzinfo.utcoffset(posted_at) is None:
            now = datetime.now()
        else:
            now = datetime.now(posted_at.tzinfo)

        max_age_days = self.runtime_config.get().settings.max_comment_age_days
        return (now - posted_at).days > max_age_days

    def _generate_reply(self, comment_id: int, comment: Comment) -> None:
        runtime_settings = self.runtime_config.get().settings
        max_reply_length = runtime_settings.max_reply_length
        auto_publish = runtime_settings.auto_publish
        publication_title = comment.publication_title or self.settings.COMMENTS_URL
        classifier_text = "\n".join(
            part for part in (comment.thread_text, comment.text) if part
        )
        reply_type = self.classify_reply_type(
            publication_title=publication_title,
            thread_text=classifier_text,
        )
        prompt = self.prompt_builder.build(
            PromptContext(
                publication_title=publication_title,
                thread_text=comment.thread_text,
                reply_type=reply_type,
                comment_text=comment.text,
            )
        )
        text = self._extract_reply_text(
            self.ai_provider.generate(
                prompt,
                temperature=self.settings.AI_TEMPERATURE,
                max_tokens=self.settings.AI_MAX_TOKENS,
            )
        )
        if not text:
            self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
            return

        if len(text) > max_reply_length:
            text = self._extract_reply_text(
                self.ai_provider.generate(
                    prompt,
                    temperature=self.settings.AI_TEMPERATURE,
                    max_tokens=self.settings.AI_MAX_TOKENS,
                )
            )

        if not text:
            self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
            return

        if len(text) > max_reply_length:
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

        try:
            self.page.publish_reply(
                comment,
                text,
                auto_publish=auto_publish,
            )
        except Exception as exc:
            self.repository.set_reply_status(
                reply_id,
                ReplyStatus.ERROR,
                "Dzen reply publication failed",
            )
            self.repository.set_comment_status(comment_id, CommentStatus.ERROR)
            self.notifier.notify_error("Dzen reply publication failed", exc)
            return

        if auto_publish:
            self.repository.set_reply_status(reply_id, ReplyStatus.PUBLISHED)

    @staticmethod
    def _extract_reply_text(raw_text: str) -> str:
        """Extract the publishable answer from the model's typed response."""
        raw_text = raw_text.strip()
        for line in raw_text.splitlines():
            normalized = line.strip().lower()
            if normalized.startswith("тип:") and "пропуск" in normalized:
                return ""
        for line in raw_text.splitlines():
            if line.strip().lower().startswith("ответ:"):
                return line.split(":", 1)[1].strip()
        return raw_text

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
