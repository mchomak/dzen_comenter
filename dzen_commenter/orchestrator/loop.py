from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dzen_commenter.config.runtime_config import RuntimeConfig
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.enums import BatchOutcomeKind, CommentStatus, ReplyStatus
from dzen_commenter.contracts.interfaces import (
    AIProvider,
    AuthAssistant,
    BatchPromptBuilder,
    BatchReplyParser,
    CommentRepository,
    DzenPage,
    Notifier,
    PromptBuilder,
    PromptContext,
    ReplyType,
    SessionManager,
)
from dzen_commenter.contracts.models import (
    BatchItem,
    BatchOutcome,
    ClaimedBatch,
    Comment,
    Publication,
    Reply,
)
from dzen_commenter.prompt.batch import BatchParseError
from dzen_commenter.time_utils import moscow_now

CTA_PROMPT_TEMPLATE = (
    "Текст CTA для этого ответа: {cta_text}\n"
    "Обязательно органично вплети этот текст в основную мысль ответа. "
    "Не выводи его отдельной строкой и не делай отдельным рекламным предложением. "
    "Не добавляй URL, Markdown-ссылки или другой текст ссылки помимо указанного CTA."
)


class OrchestratorLoop:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: CommentRepository,
        ai_provider: AIProvider,
        prompt_builder: PromptBuilder,
        batch_prompt_builder: BatchPromptBuilder,
        batch_reply_parser: BatchReplyParser,
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
        self.batch_prompt_builder = batch_prompt_builder
        self.batch_reply_parser = batch_reply_parser
        self.session = session
        self.page = page
        self.notifier = notifier
        self.auth_assistant = auth_assistant
        self.classify_reply_type = classify_reply_type
        self.is_cta_candidate_title = is_cta_candidate_title
        self.runtime_config = runtime_config
        self.sleep_fn = sleep_fn
        self._authorization_not_confirmed_notified = False

    def run_cycle(self) -> None:
        if self.auth_assistant.poll_auth_command():
            with self._browser_access():
                self.session.reset_authentication()
            self.auth_assistant.reset_ready_prompt()

        with self._browser_access():
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

        with self._browser_access():
            comments = self.page.fetch_comments()
        indexed_comments: list[tuple[int, Comment]] = []
        for comment in comments:
            comment.publication_id = publication_id
            comment_id = self.repository.upsert_comment(comment)
            comment.id = comment_id
            indexed_comments.append((comment_id, comment))

        runtime_settings = self.runtime_config.get().settings
        if runtime_settings.batch_replies_enabled:
            self._run_batch_cycle(indexed_comments, runtime_settings)
            return

        generated_replies = 0
        for comment_id, comment in indexed_comments:
            if generated_replies >= self.settings.MAX_REPLIES_PER_CYCLE:
                break

            runtime_settings = self.runtime_config.get().settings
            attempts_since = self.repository.count_ai_attempts_since(
                moscow_now() - timedelta(hours=1)
            )
            if attempts_since >= runtime_settings.max_comments_per_hour:
                break

            if self.repository.has_generated_reply(comment_id):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue

            if self.repository.is_own_reply(comment.post_url, comment.text):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue

            if self._is_too_old(comment.posted_at):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue

            self._generate_reply(comment_id, comment)
            generated_replies += 1

    def _run_batch_cycle(
        self, indexed_comments: list[tuple[int, Comment]], runtime_settings
    ) -> None:
        cutover_at = self._batch_cutover_at(runtime_settings.batch_cutover_at)
        if cutover_at is None:
            return

        fallback_comments: list[tuple[int, Comment]] = []
        queued_at = moscow_now()
        for comment_id, comment in indexed_comments:
            if self.repository.has_generated_reply(comment_id):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue
            if self.repository.is_own_reply(comment.post_url, comment.text):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue
            if self._is_too_old(comment.posted_at):
                self.repository.set_comment_status(comment_id, CommentStatus.SKIPPED)
                continue
            if comment.post_url:
                self.repository.enqueue_batch_comment(
                    comment_id,
                    comment.post_url,
                    queued_at=queued_at,
                    cutover_at=cutover_at,
                )
            else:
                fallback_comments.append((comment_id, comment))

        attempts_since = self.repository.count_ai_attempts_since(
            queued_at - timedelta(hours=1)
        )
        quota_remaining = runtime_settings.max_comments_per_hour - attempts_since
        batch = self.repository.claim_next_batch(
            queued_at,
            max_comments=min(
                runtime_settings.batch_max_comments,
                self.settings.MAX_REPLIES_PER_CYCLE,
            ),
            wait_hours=runtime_settings.batch_wait_hours,
            quota_remaining=quota_remaining,
        )
        processed_items = 0
        if batch is not None:
            comments_by_id = dict(indexed_comments)
            self._generate_batch(batch, comments_by_id, runtime_settings)
            processed_items = len(batch.items)

        self._generate_fallback_comments(
            fallback_comments,
            processed_items=processed_items,
            quota_remaining=quota_remaining,
        )

    def _generate_fallback_comments(
        self,
        comments: list[tuple[int, Comment]],
        *,
        processed_items: int,
        quota_remaining: int,
    ) -> None:
        limit = min(self.settings.MAX_REPLIES_PER_CYCLE, max(quota_remaining, 0))
        for comment_id, comment in comments[: max(limit - processed_items, 0)]:
            self._generate_reply(comment_id, comment)

    def _generate_batch(
        self,
        batch: ClaimedBatch,
        comments_by_id: dict[int, Comment],
        runtime_settings,
    ) -> None:
        try:
            with self._browser_access():
                article_text = self.page.fetch_article_text(batch.post_url)
        except Exception as exc:
            article_text = None
            self.notifier.notify_error("Dzen article text extraction failed", exc)
        article_context_status = (
            "article_text_used" if article_text else "without_article_text"
        )
        try:
            prompt = self.batch_prompt_builder.build_batch(
                batch.items,
                article_text=article_text or "",
            )
            raw = self.ai_provider.generate(
                prompt,
                temperature=self.settings.AI_TEMPERATURE,
                max_tokens=self.settings.AI_MAX_TOKENS,
            )
            outcomes = self.batch_reply_parser(
                raw,
                batch.items,
                runtime_settings.max_reply_length,
            )
        except Exception as exc:
            if len(batch.items) > 1 and isinstance(exc, BatchParseError):
                outcomes = self._generate_single_item_batch_outcomes(
                    batch.items,
                    article_text=article_text or "",
                    max_reply_length=runtime_settings.max_reply_length,
                )
            else:
                outcomes = self._batch_error_outcomes(batch.items, exc)

        reply_ids = self.repository.save_batch_outcomes(
            batch.id,
            outcomes,
            ai_provider=self.settings.AI_PROVIDER,
            ai_model=self.settings.AI_MODEL,
            article_context_status=article_context_status,
            created_at=moscow_now(),
            prompt_tokens=None,
            completion_tokens=None,
            retry_cooldown_minutes=runtime_settings.batch_retry_cooldown_minutes,
            max_attempts_per_comment=runtime_settings.batch_max_attempts_per_comment,
        )
        for outcome, reply_id in zip(outcomes, reply_ids, strict=True):
            if outcome.kind is not BatchOutcomeKind.REPLY:
                continue
            comment = comments_by_id.get(outcome.comment_id)
            if comment is None:
                reason = "Dzen comment is unavailable for batch reply publication"
                self.repository.set_reply_status(reply_id, ReplyStatus.ERROR, reason)
                self.repository.set_comment_status(
                    outcome.comment_id, CommentStatus.ERROR
                )
                self.notifier.notify_error(reason)
                continue
            try:
                with self._browser_access():
                    self.page.publish_reply(
                        comment,
                        outcome.text,
                        auto_publish=runtime_settings.auto_publish,
                    )
            except Exception as exc:
                self.repository.set_reply_status(
                    reply_id,
                    ReplyStatus.ERROR,
                    "Dzen reply publication failed",
                )
                self.repository.set_comment_status(
                    outcome.comment_id, CommentStatus.ERROR
                )
                self.notifier.notify_error("Dzen reply publication failed", exc)
                continue

            if runtime_settings.auto_publish:
                self.repository.set_reply_status(
                    reply_id,
                    ReplyStatus.PUBLISHED,
                    published_at=moscow_now(),
                )

    def _generate_single_item_batch_outcomes(
        self,
        items: tuple[BatchItem, ...],
        *,
        article_text: str,
        max_reply_length: int,
    ) -> tuple[BatchOutcome, ...]:
        outcomes: list[BatchOutcome] = []
        for item in items:
            single_item = BatchItem(
                batch_id=item.batch_id,
                comment_id=item.comment_id,
                item_no=1,
                post_url=item.post_url,
                publication_title=item.publication_title,
                thread_text=item.thread_text,
                author=item.author,
                comment_text=item.comment_text,
            )
            try:
                prompt = self.batch_prompt_builder.build_batch(
                    (single_item,),
                    article_text=article_text,
                )
                raw = self.ai_provider.generate(
                    prompt,
                    temperature=self.settings.AI_TEMPERATURE,
                    max_tokens=self.settings.AI_MAX_TOKENS,
                )
                outcome = self.batch_reply_parser(
                    raw,
                    (single_item,),
                    max_reply_length,
                )[0]
            except Exception as exc:
                outcomes.extend(self._batch_error_outcomes((item,), exc))
                continue
            outcomes.append(
                BatchOutcome(
                    comment_id=item.comment_id,
                    item_no=item.item_no,
                    kind=outcome.kind,
                    text=outcome.text,
                    error_reason=outcome.error_reason,
                )
            )
        return tuple(outcomes)

    def _batch_error_outcomes(
        self, items: tuple[BatchItem, ...], exc: Exception
    ) -> tuple[BatchOutcome, ...]:
        reason = f"Batch reply generation failed: {exc}"
        outcomes = tuple(
            BatchOutcome(
                comment_id=item.comment_id,
                item_no=item.item_no,
                kind=BatchOutcomeKind.ERROR,
                error_reason=reason,
            )
            for item in items
        )
        self.notifier.notify_error(reason, exc)
        return outcomes

    @staticmethod
    def _batch_cutover_at(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            cutover_at = datetime.fromisoformat(value)
        except ValueError:
            return None
        if cutover_at.tzinfo is None:
            return None
        return cutover_at.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)

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

        now = moscow_now()

        max_age_days = self.runtime_config.get().settings.max_comment_age_days
        return (now - posted_at).days > max_age_days

    def _generate_reply(self, comment_id: int, comment: Comment) -> None:
        runtime_settings = self.runtime_config.get().settings
        max_reply_length = runtime_settings.max_reply_length
        auto_publish = runtime_settings.auto_publish
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
        with self._browser_access():
            article_text = (
                self.page.fetch_article_text(comment.post_url)
                if comment.post_url
                else None
            )
        article_context_status = (
            "article_text_used" if article_text else "without_article_text"
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
                article_text=article_text or "",
            )
        )
        if cta_instruction:
            prompt += f"\n\n{cta_instruction}"
            prompt += f"\n\nДлина ответа: не более {model_reply_length} символов."
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

        text = self._format_reply_text(text, author_prefix)

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

            text = self._format_reply_text(text, author_prefix)

        if len(text) > max_reply_length:
            reason = "reply too long after regeneration"
            self.repository.save_reply(
                self._make_reply(
                    comment_id=comment_id,
                    text=text,
                    status=ReplyStatus.ERROR,
                    error_reason=reason,
                    article_context_status=article_context_status,
                    is_cta_candidate=is_cta_candidate,
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
                article_context_status=article_context_status,
                is_cta_candidate=is_cta_candidate,
            )
        )
        self.repository.set_comment_status(comment_id, CommentStatus.ANSWERED)

        try:
            with self._browser_access():
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
            self.repository.set_reply_status(
                reply_id,
                ReplyStatus.PUBLISHED,
                published_at=moscow_now(),
            )

    def _browser_access(self):
        browser_access = getattr(self.session, "browser_access", None)
        return browser_access() if browser_access is not None else nullcontext()

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

    @staticmethod
    def _format_reply_text(text: str, author_prefix: str) -> str:
        for index, character in enumerate(text):
            if character.isalpha():
                text = text[:index] + character.lower() + text[index + 1 :]
                break
        return f"{author_prefix}{text}"

    def _make_reply(
        self,
        *,
        comment_id: int,
        text: str,
        status: ReplyStatus,
        error_reason: str | None,
        article_context_status: str | None = None,
        is_cta_candidate: bool = False,
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
            created_at=moscow_now(),
            article_context_status=article_context_status,
            is_cta_candidate=is_cta_candidate,
        )
