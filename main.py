from __future__ import annotations

import logging
import time
from collections.abc import Callable

import sqlalchemy

from dzen_commenter.ai.factory import create_provider
from dzen_commenter.auth.telegram_auth_assistant import TelegramAuthAssistant
from dzen_commenter.browser.session_manager import PlaywrightSessionManager
from dzen_commenter.config.runtime_config import RuntimeConfig, ensure_runtime_config
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.interfaces import Notifier
from dzen_commenter.db.repository import PostgresCommentRepository
from dzen_commenter.dzen.page import DzenStudioPage
from dzen_commenter.monitoring.email_fallback import EmailFallbackNotifier
from dzen_commenter.monitoring.logging_config import configure_logging
from dzen_commenter.monitoring.telegram_notifier import TelegramNotifier
from dzen_commenter.monitoring.developer_notifier import DeveloperNotifier
from dzen_commenter.orchestrator.loop import OrchestratorLoop
from dzen_commenter.prompt.builder import DameoPromptBuilder
from dzen_commenter.prompt.classifier import classify_reply_type, is_cta_candidate_title
from dzen_commenter.prompt.config_loader import load_brand_config


_ERROR_NOTIFICATION_COOLDOWN = 15 * 60
logger = logging.getLogger(__name__)


def build_app(
    settings: Settings,
) -> tuple[OrchestratorLoop, PlaywrightSessionManager, Notifier]:
    engine = sqlalchemy.create_engine(settings.DATABASE_URL)
    repository = PostgresCommentRepository(engine)

    ai_provider = create_provider(settings)

    runtime_config = RuntimeConfig(settings.RUNTIME_CONFIG_PATH)
    ensure_runtime_config(
        settings.RUNTIME_CONFIG_PATH,
        settings,
        load_brand_config(None),
    )

    prompt_builder = DameoPromptBuilder(
        language=settings.AI_PROMPT_LANGUAGE,
        config_provider=lambda: runtime_config.get().prompt,
    )

    auth_assistant = TelegramAuthAssistant(
        bot_token=settings.TELEGRAM_BOT_TOKEN,
        chat_id=settings.TELEGRAM_CHAT_ID,
        proxy_url=settings.TELEGRAM_PROXY_URL,
        proxy_url_provider=lambda: runtime_config.get().settings.telegram_proxy_url,
    )

    session = PlaywrightSessionManager(settings, auth_assistant=auth_assistant)
    session.start()
    page = DzenStudioPage(lambda: session.page)

    if settings.SMTP_HOST:
        email_fallback = EmailFallbackNotifier(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            user=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            from_addr=settings.SMTP_FROM,
            to_addrs=[],
            to_addrs_provider=lambda: [
                a.strip()
                for a in runtime_config.get().settings.error_email_list.split(",")
                if a.strip()
            ],
        )
    else:
        email_fallback = None

    notifier = DeveloperNotifier(TelegramNotifier(
        bot_token=settings.TELEGRAM_BOT_TOKEN,
        chat_id="",
        proxy_url=settings.TELEGRAM_PROXY_URL,
        fallback=email_fallback,
        chat_id_provider=lambda: runtime_config.get().settings.developer_telegram_chat_ids,
        proxy_url_provider=lambda: runtime_config.get().settings.telegram_proxy_url,
    ))

    loop = OrchestratorLoop(
        settings=settings,
        repository=repository,
        ai_provider=ai_provider,
        prompt_builder=prompt_builder,
        session=session,
        page=page,
        notifier=notifier,
        auth_assistant=auth_assistant,
        classify_reply_type=classify_reply_type,
        is_cta_candidate_title=is_cta_candidate_title,
        runtime_config=runtime_config,
    )

    return loop, session, notifier


def run_supervised(
    loop: OrchestratorLoop,
    session: PlaywrightSessionManager,
    notifier: Notifier,
    *,
    poll_interval: float,
    keepalive_interval: float,
    sleep_fn=time.sleep,
    time_fn=time.monotonic,
    error_notification_cooldown_provider: Callable[[], int] = (
        lambda: _ERROR_NOTIFICATION_COOLDOWN
    ),
    max_cycles: int | None = None,
) -> None:
    last_keepalive = time_fn()
    last_error_signature: tuple[type[Exception], str] | None = None
    last_error_notification_at: float | None = None
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        try:
            loop.run_cycle()
        except Exception as exc:
            logger.warning(
                "Comment-processing cycle failed; retrying after the poll interval",
                exc_info=True,
                extra={
                    "event": "main_loop_cycle_failed",
                    "cycle": cycles + 1,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            now = time_fn()
            error_signature = (type(exc), str(exc))
            repeated_error = (
                error_signature == last_error_signature
                and last_error_notification_at is not None
            )
            if (
                not repeated_error
                or now - last_error_notification_at
                >= error_notification_cooldown_provider()
            ):
                notifier.notify_error(
                    "Comment-processing cycle failed; retrying after the poll interval",
                    exc,
                )
                last_error_signature = error_signature
                last_error_notification_at = now
        now = time_fn()
        if now - last_keepalive >= keepalive_interval:
            try:
                session.keep_alive()
            except Exception as exc:
                notifier.notify_error("Keep-alive failed", exc)
            last_keepalive = now
        sleep_fn(poll_interval)
        cycles += 1


def main() -> None:
    configure_logging()
    settings = Settings()
    loop, session, notifier = build_app(settings)
    configure_logging(notifier=notifier)
    run_supervised(
        loop,
        session,
        notifier,
        poll_interval=settings.POLL_INTERVAL,
        keepalive_interval=settings.KEEPALIVE_INTERVAL,
        error_notification_cooldown_provider=lambda: (
            loop.runtime_config.get().settings.error_notification_cooldown_seconds
        ),
    )


if __name__ == "__main__":
    main()
