from __future__ import annotations

import time

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
from dzen_commenter.prompt.classifier import classify_reply_type
from dzen_commenter.prompt.config_loader import load_brand_config


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
        load_brand_config(settings.PROMPT_CONFIG_PATH or None),
    )

    prompt_builder = DameoPromptBuilder(
        language=settings.AI_PROMPT_LANGUAGE,
        config_provider=lambda: runtime_config.get().prompt,
    )

    auth_assistant = TelegramAuthAssistant(
        bot_token=settings.TELEGRAM_BOT_TOKEN,
        chat_id=settings.TELEGRAM_CHAT_ID,
        proxy_url=settings.TELEGRAM_PROXY_URL,
    )

    session = PlaywrightSessionManager(settings, auth_assistant=auth_assistant)
    session.start()
    page = DzenStudioPage(session.page)

    if settings.SMTP_HOST:
        email_fallback = EmailFallbackNotifier(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            user=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            from_addr=settings.SMTP_FROM,
            to_addrs=[
                a.strip()
                for a in settings.EMAIL_FALLBACK_LIST.split(",")
                if a.strip()
            ],
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
        chat_id=settings.DEVELOPER_TELEGRAM_CHAT_ID_LIST,
        proxy_url=settings.TELEGRAM_PROXY_URL,
        fallback=email_fallback,
        chat_id_provider=lambda: runtime_config.get().settings.developer_telegram_chat_ids,
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
    max_cycles: int | None = None,
) -> None:
    last_keepalive = time_fn()
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        try:
            loop.run_cycle()
        except Exception as exc:
            notifier.notify_error("Unhandled error in main loop", exc)
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
    )


if __name__ == "__main__":
    main()
