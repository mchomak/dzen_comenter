from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta

from dzen_commenter.config.runtime_config import RuntimeConfig, is_bot_account_author
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.enums import PublicationFailureOutcome
from dzen_commenter.contracts.errors import (
    PublicationUnconfirmedError,
    SourceCommentUnavailableError,
)
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
from dzen_commenter.contracts.models import Comment, Publication
from dzen_commenter.contracts.reply_text import sanitize_model_reply
from dzen_commenter.time_utils import moscow_now

CTA_PROMPT_TEMPLATE = (
    "Текст CTA для этого ответа: {cta_text}\n"
    "Обязательно органично вплети этот текст в основную мысль ответа. "
    "Не выводи его отдельной строкой и не делай отдельным рекламным предложением. "
    "Не добавляй URL, Markdown-ссылки или другой текст ссылки помимо указанного CTA."
)

logger = logging.getLogger(__name__)


class _HourlyAIAttemptLimitReached(Exception):
    pass


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
        is_cta_candidate_title: Callable[[str], bool],
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
        self.is_cta_candidate_title = is_cta_candidate_title
        self.runtime_config = runtime_config
        self.sleep_fn = sleep_fn
        self._authorization_not_confirmed_notified = False
        self._active_publication_lock = threading.RLock()
        self._active_publication: tuple[int, str] | None = None

    def active_publication_attempt(self) -> tuple[int, str] | None:
        """Return the active durable submit claim without exposing mutable state."""
        with self._active_publication_lock:
            return self._active_publication

    def mark_active_publication_unconfirmed(self, *, reason: str) -> bool:
        """Persist an unknown submit outcome before a stale worker is stopped."""
        with self._active_publication_lock:
            attempt = self._active_publication
            if attempt is None:
                return False
            reply_id, claim_token = attempt
            self.repository.mark_publication_unconfirmed(
                reply_id,
                claim_token=claim_token,
                reason=reason,
            )
            if self._active_publication == attempt:
                self._active_publication = None
            return True

    def _set_active_publication_attempt(
        self, attempt: tuple[int, str] | None
    ) -> None:
        with self._active_publication_lock:
            self._active_publication = attempt

    def run_cycle(self) -> None:
        if self.auth_assistant.poll_auth_command():
            with self._browser_access():
                self.session.reset_authentication()
            self.auth_assistant.reset_ready_prompt()

        with self._browser_access():
            if not self._ensure_session():
                return

        runtime_settings = self.runtime_config.get().settings
        now = moscow_now()
        self.repository.enqueue_pending_generations(queued_at=now)
        self._drain_generation_queue(runtime_settings, max_jobs=5)
        self._run_publication_cycle(runtime_settings, max_publications=1)

        publication_id = self.repository.upsert_publication(
            Publication(
                id=None,
                dzen_publication_id=self.settings.COMMENTS_URL,
                title=self.settings.COMMENTS_URL,
                url=self.settings.COMMENTS_URL,
            )
        )

        with self._browser_access():
            comments = self.page.fetch_comments()
        for comment in comments:
            if is_bot_account_author(
                comment.author, runtime_settings.bot_account_name
            ):
                continue
            comment.publication_id = publication_id
            if self.repository.is_own_reply(comment.post_url, comment.text) or self._is_too_old(
                comment.posted_at
            ):
                comment_id = self.repository.upsert_comment(comment)
                self.repository.skip_comment_if_new(comment_id)
            else:
                comment_id = self.repository.upsert_eligible_comment(
                    comment, queued_at=now
                )
            comment.id = comment_id
            if self.repository.has_published_reply(comment_id):
                continue

    def _drain_generation_queue(self, runtime_settings, *, max_jobs: int) -> int:
        processed = 0
        for _ in range(max_jobs):
            now = moscow_now()
            if self.repository.count_ai_attempts_since(
                now - timedelta(hours=1)
            ) >= runtime_settings.max_comments_per_hour:
                break

            job_ai_attempts = 0

            def generate_with_quota(prompt: str) -> str:
                nonlocal job_ai_attempts
                attempt_at = moscow_now()
                if not self.repository.reserve_ai_call(
                    attempt_at, limit=runtime_settings.max_comments_per_hour
                ):
                    if job_ai_attempts == 0:
                        raise _HourlyAIAttemptLimitReached(
                            "Hourly AI attempt limit reached before the model call"
                        )
                    raise RuntimeError(
                        "Hourly AI attempt limit reached before the shortening retry"
                    )
                job_ai_attempts += 1
                return self.ai_provider.generate(
                    prompt,
                    temperature=self.settings.AI_TEMPERATURE,
                    max_tokens=self.settings.AI_MAX_TOKENS,
                )

            if not self._run_generation_cycle(
                runtime_settings, ai_generate=generate_with_quota
            ):
                break
            processed += 1
        return processed

    def _run_publication_cycle(
        self,
        runtime_settings,
        *,
        max_publications: int,
    ) -> int:
        now = moscow_now()
        oldest_allowed_comment_fetched_at = now - timedelta(
            days=runtime_settings.max_comment_age_days
        )
        expired_count = self.repository.expire_stale_publications(
            now,
            oldest_allowed_comment_fetched_at,
        )
        if expired_count:
            logger.info(
                "Expired stale reply publications",
                extra={
                    "event": "publication_stale_expired",
                    "expired_count": expired_count,
                },
            )

        publication_attempts = 0
        for _ in range(max_publications):
            claimed = self.repository.claim_next_publication(moscow_now())
            if claimed is None:
                return publication_attempts
            publication_attempts += 1
            correlation = {
                "comment_id": claimed.comment.dzen_comment_id,
                "reply_id": claimed.reply_id,
            }
            logger.info(
                "Dzen publication job claimed",
                extra={
                    "event": "publication_job_started",
                    **correlation,
                    "auto_publish": runtime_settings.auto_publish,
                },
            )
            try:
                def before_submit() -> None:
                    try:
                        self.repository.mark_publication_submitting(
                            claimed.reply_id, claim_token=claimed.claim_token
                        )
                        self._set_active_publication_attempt(
                            (claimed.reply_id, claimed.claim_token)
                        )
                    except Exception as marker_error:
                        raise RuntimeError(
                            "publication submit marker failed before click"
                        ) from marker_error

                with self._browser_access():
                    self.page.publish_reply(
                        claimed.comment,
                        claimed.text,
                        auto_publish=runtime_settings.auto_publish,
                        reply_id=claimed.reply_id,
                        before_submit=before_submit,
                    )
            except PublicationUnconfirmedError as exc:
                if not self.mark_active_publication_unconfirmed(reason=str(exc)):
                    self.repository.mark_publication_unconfirmed(
                        claimed.reply_id,
                        claim_token=claimed.claim_token,
                        reason=str(exc),
                    )
                logger.warning(
                    "Dzen reply publication was not confirmed",
                    extra={
                        "event": "publication_unconfirmed",
                        **correlation,
                        "failure_type": type(exc).__name__,
                    },
                )
                continue
            except Exception as exc:
                if self.active_publication_attempt() == (
                    claimed.reply_id,
                    claimed.claim_token,
                ):
                    self.mark_active_publication_unconfirmed(
                        reason="publication outcome unknown after submit failure"
                    )
                    logger.warning(
                        "Dzen reply publication outcome is unknown",
                        extra={
                            "event": "publication_unconfirmed",
                            **correlation,
                            "failure_type": type(exc).__name__,
                        },
                    )
                    continue

                error_reason = f"Dzen reply publication failed: {exc}"
                outcome = self.repository.fail_publication(
                    claimed.reply_id,
                    claim_token=claimed.claim_token,
                    error_reason=error_reason,
                    failed_at=moscow_now(),
                    retry_cooldown_minutes=(
                        runtime_settings.publication_retry_cooldown_minutes
                    ),
                    max_attempts_per_reply=(
                        runtime_settings.publication_max_attempts_per_reply
                    ),
                )
                if outcome is PublicationFailureOutcome.RETRY:
                    logger.warning(
                        "Dzen reply publication retry scheduled",
                        extra={
                            "event": "publication_retry",
                            **correlation,
                            "failure_type": type(exc).__name__,
                        },
                    )
                elif isinstance(exc, SourceCommentUnavailableError):
                    logger.warning(
                        "Dzen reply source comment unavailable after retries",
                        extra={
                            "event": "publication_source_unavailable",
                            **correlation,
                            "failure_type": type(exc).__name__,
                        },
                    )
                else:
                    logger.error(
                        "Dzen reply publication failed",
                        exc_info=exc,
                        extra={
                            "event": "publication_terminal_failure",
                            **correlation,
                            "failure_type": type(exc).__name__,
                        },
                    )
                logger.info(
                    "Dzen publication job failed",
                    extra={
                        "event": "publication_failed",
                        **correlation,
                        "failure_type": type(exc).__name__,
                        "outcome": outcome.value,
                    },
                )
                continue

            published_at = moscow_now() if runtime_settings.auto_publish else None
            with self._active_publication_lock:
                self.repository.complete_publication(
                    claimed.reply_id,
                    claim_token=claimed.claim_token,
                    published_at=published_at,
                )
                self._active_publication = None
            logger.info(
                "Dzen publication job completed",
                extra={
                    "event": "publication_completed",
                    **correlation,
                    "outcome": "published" if published_at is not None else "draft",
                },
            )
        return publication_attempts

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

        if self.settings.DZEN_LOGIN_PHONE and self.settings.DZEN_LOGIN_PASSWORD:
            try:
                if self.session.login():
                    return self._session_ready()
            except Exception as exc:
                self.notifier.notify_error("Dzen automated login failed", exc)

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

        now = moscow_now()

        max_age_days = self.runtime_config.get().settings.max_comment_age_days
        return (now - posted_at).days > max_age_days

    def _run_generation_cycle(
        self, runtime_settings, *, ai_generate: Callable[[str], str]
    ) -> bool:
        claimed = self.repository.claim_next_generation(moscow_now())
        if claimed is None:
            return False

        comment = claimed.comment
        comment_id = comment.id
        if comment_id is None:
            raise ValueError("Claimed generation comment has no id")
        if self.repository.is_own_reply(comment.post_url, comment.text):
            self._skip_generation(
                comment_id,
                claim_token=claimed.claim_token,
                reason="Comment is an existing own reply",
                article_context_status="without_article_text",
            )
            return True
        if self._is_too_old(comment.posted_at):
            self._skip_generation(
                comment_id,
                claim_token=claimed.claim_token,
                reason="Comment is older than the configured limit",
                article_context_status="without_article_text",
            )
            return True

        article_text, article_context_status = self._get_article_text(comment)
        try:
            self._generate_reply(
                comment,
                runtime_settings,
                claim_token=claimed.claim_token,
                article_text=article_text,
                article_context_status=article_context_status,
                ai_generate=ai_generate,
            )
        except _HourlyAIAttemptLimitReached:
            self.repository.release_generation_claim(
                comment_id, claim_token=claimed.claim_token
            )
            return False
        return True

    def _get_article_text(self, comment: Comment) -> tuple[str, str]:
        cached_context = self.repository.get_article_context(comment.publication_id)
        if cached_context is not None and cached_context.text:
            return cached_context.text, "article_text_used"

        article_text: str | None = None
        context_status = "without_article_text"
        try:
            if comment.post_url:
                with self._browser_access():
                    article_text = self.page.fetch_article_text(comment.post_url)
            if article_text:
                context_status = "article_text_used"
        except Exception as exc:
            context_status = "article_text_error"
            self.notifier.notify_error("Dzen article text extraction failed", exc)

        try:
            self.repository.save_article_context(
                comment.publication_id,
                text=article_text,
                status=context_status,
                fetched_at=moscow_now(),
            )
        except Exception as exc:
            self.notifier.notify_error("Dzen article context persistence failed", exc)
        return article_text or "", context_status

    def _generate_reply(
        self,
        comment: Comment,
        runtime_settings,
        *,
        claim_token: str,
        article_text: str,
        article_context_status: str,
        ai_generate: Callable[[str], str],
    ) -> None:
        comment_id = comment.id
        if comment_id is None:
            raise ValueError("Generation comment has no id")
        max_reply_length = runtime_settings.max_reply_length
        publication_title = comment.publication_title or self.settings.COMMENTS_URL
        is_cta_candidate = self.is_cta_candidate_title(publication_title)
        cta_instruction = ""
        if is_cta_candidate:
            produced_candidates = self.repository.count_cta_candidates_produced()
            if (produced_candidates + 1) % runtime_settings.cta_every_n_comments == 0:
                cta_instruction = CTA_PROMPT_TEMPLATE.format(
                    cta_text=self.runtime_config.get().prompt.cta_link
                )
        author_prefix = f"{comment.author.strip()}, " if comment.author.strip() else ""
        model_reply_length = max_reply_length - len(author_prefix)
        try:
            if model_reply_length <= 0:
                raise ValueError(
                    "Configured reply length leaves no room for a reply after the author prefix"
                )
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
                    post_url=comment.post_url,
                    article_text=article_text,
                )
            )
            if cta_instruction:
                prompt += f"\n\n{cta_instruction}"
            prompt += (
                "\n\nОграничение длины: текст ответа без обращения к автору — "
                f"не более {model_reply_length} символов."
            )
            text = self._extract_reply_text(
                ai_generate(prompt)
            )
            if text is None:
                self._skip_generation(
                    comment_id,
                    claim_token=claim_token,
                    reason="Model returned SKIP",
                    article_context_status=article_context_status,
                )
                return
            if not text:
                raise ValueError("Model reply is empty or protocol-only")
            text = self._format_reply_text(text, author_prefix)
            if len(text) > max_reply_length:
                retry_prompt = (
                    f"{prompt}\n\n"
                    "Предыдущий вариант превысил ограничение длины. Сформулируй короче, "
                    f"не более {model_reply_length} символов до добавления обращения к автору. "
                    "Сохрани смысл и соблюдай все ограничения выше. Верни только готовый "
                    "текст ответа или ровно SKIP."
                )
                text = self._extract_reply_text(
                    ai_generate(retry_prompt)
                )
                if text is None:
                    self._skip_generation(
                        comment_id,
                        claim_token=claim_token,
                        reason="Model returned SKIP",
                        article_context_status=article_context_status,
                    )
                    return
                if not text:
                    raise ValueError("Model reply is empty or protocol-only")
                text = self._format_reply_text(text, author_prefix)
                if len(text) > max_reply_length:
                    raise ValueError(
                        "Model reply still exceeds the configured length after one "
                        f"shortening retry ({len(text)} > {max_reply_length} characters)"
                    )
            self.repository.complete_generation(
                comment_id,
                claim_token=claim_token,
                text=text,
                ai_provider=self.settings.AI_PROVIDER,
                ai_model=self.settings.AI_MODEL,
                article_context_status=article_context_status,
                created_at=moscow_now(),
                is_cta_candidate=is_cta_candidate,
            )
        except _HourlyAIAttemptLimitReached:
            raise
        except Exception as exc:
            error_reason = f"Reply generation failed: {exc}"
            self.repository.fail_generation(
                comment_id=comment_id,
                claim_token=claim_token,
                error_reason=error_reason,
                failed_at=moscow_now(),
                ai_provider=self.settings.AI_PROVIDER,
                ai_model=self.settings.AI_MODEL,
                article_context_status=article_context_status,
                retry_cooldown_minutes=runtime_settings.generation_retry_cooldown_minutes,
                max_attempts_per_comment=(
                    runtime_settings.generation_max_attempts_per_comment
                ),
            )
            self.notifier.notify_error(error_reason, exc)

    def _skip_generation(
        self,
        comment_id: int,
        *,
        claim_token: str,
        reason: str,
        article_context_status: str,
    ) -> None:
        self.repository.skip_generation(
            comment_id,
            claim_token=claim_token,
            reason=reason,
            ai_provider=self.settings.AI_PROVIDER,
            ai_model=self.settings.AI_MODEL,
            article_context_status=article_context_status,
            created_at=moscow_now(),
        )

    def _browser_access(self):
        browser_access = getattr(self.session, "browser_access", None)
        return browser_access() if browser_access is not None else nullcontext()

    @staticmethod
    def _extract_reply_text(raw_text: str) -> str | None:
        """Extract the publishable answer from the model's typed response."""
        return sanitize_model_reply(raw_text)

    @staticmethod
    def _format_reply_text(text: str, author_prefix: str) -> str:
        for index, character in enumerate(text):
            if character.isalpha():
                text = text[:index] + character.lower() + text[index + 1 :]
                break
        return f"{author_prefix}{text}"
