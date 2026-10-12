from __future__ import annotations

import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)
from sqlalchemy.exc import OperationalError

from dzen_commenter.bot_health import (
    get_bot_health,
    write_bot_health,
    write_bot_progress,
)
from dzen_commenter.ai.factory import create_provider
from dzen_commenter.auth.dzen_login_control import DzenLoginControlServer
from dzen_commenter.auth.telegram_auth_assistant import TelegramAuthAssistant
from dzen_commenter.browser.session_manager import PlaywrightSessionManager
from dzen_commenter.config.runtime_config import RuntimeConfig, ensure_runtime_config
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.interfaces import Notifier
from dzen_commenter.db import create_database_engine
from dzen_commenter.db.repository import PostgresCommentRepository
from dzen_commenter.dzen.page import DzenStudioPage
from dzen_commenter.monitoring.developer_notifier import DeveloperNotifier
from dzen_commenter.monitoring.email_fallback import EmailFallbackNotifier
from dzen_commenter.monitoring.logging_config import configure_logging
from dzen_commenter.monitoring.telegram_notifier import TelegramNotifier
from dzen_commenter.orchestrator.loop import OrchestratorLoop
from dzen_commenter.prompt.builder import DameoPromptBuilder
from dzen_commenter.prompt.classifier import classify_reply_type, is_cta_candidate_title
from dzen_commenter.prompt.config_loader import load_brand_config

_ERROR_NOTIFICATION_COOLDOWN = 15 * 60
_STALE_CYCLE_SHUTDOWN_SECONDS = 35 * 60
_WATCHDOG_POLL_SECONDS = 5.0
_MAX_OPERATION_ATTEMPTS = 3
logger = logging.getLogger(__name__)


def _terminate_worker_process() -> None:
    """Ask the container runtime to restart this process after durable recovery."""
    os.kill(os.getpid(), signal.SIGTERM)


def _wait_for_watchdog_stop(stop_event: threading.Event, timeout: float) -> bool:
    return stop_event.wait(timeout)


def _safe_error_fields(
    error: Exception, *, phase: str, operation: str
) -> dict[str, object]:
    if isinstance(error, (TimeoutError, PlaywrightTimeoutError)):
        reason = "timeout"
    elif isinstance(error, OperationalError):
        reason = "database_operational_error"
    else:
        reason = "operation_failed"
    return {
        "phase": phase,
        "operation": operation,
        "reason": reason,
        "error_type": type(error).__name__,
    }


def _is_renderer_crash_error(error: Exception) -> bool:
    if not isinstance(error, PlaywrightError):
        return False
    message = str(error).lower()
    return any(
        marker in message
        for marker in ("target crashed", "page crashed", "renderer crashed")
    )


def build_app(
    settings: Settings,
) -> tuple[OrchestratorLoop, PlaywrightSessionManager, Notifier]:
    engine = create_database_engine(settings.DATABASE_URL)
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
    )

    session = PlaywrightSessionManager(settings, auth_assistant=auth_assistant)
    session.start()
    page = DzenStudioPage(
        lambda: session.page,
        bot_account_name_provider=lambda: runtime_config.get().settings.bot_account_name,
        progress_callback=lambda: write_bot_progress(settings.BOT_HEALTH_PATH),
    )

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

    notifier = DeveloperNotifier(
        TelegramNotifier(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id="",
            proxy_url=settings.TELEGRAM_PROXY_URL,
            fallback=email_fallback,
            chat_id_provider=lambda: runtime_config.get().settings.developer_telegram_chat_ids,
        ),
        error_cooldown_provider=lambda: (
            runtime_config.get().settings.error_notification_cooldown_seconds
        ),
        cooldown_state_path=str(
            Path(settings.RUNTIME_CONFIG_PATH).with_name(
                "error_notification_cooldown.json"
            )
        ),
    )

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
    wall_time_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    watchdog_wait_fn: Callable[[threading.Event, float], bool] = (
        _wait_for_watchdog_stop
    ),
    process_terminate_fn: Callable[[], None] = _terminate_worker_process,
    max_cycles: int | None = None,
) -> None:
    last_keepalive = time_fn()
    last_error_signature: tuple[type[Exception], str] | None = None
    last_error_notification_at: float | None = None
    cycles = 0
    cycle_failure_count = 0
    skip_next_cycle = False
    keepalive_failure_count = 0
    skip_next_keepalive = False
    health_path = getattr(getattr(loop, "settings", None), "BOT_HEALTH_PATH", None)
    watchdog_stop = threading.Event()
    stale_shutdown_requested = threading.Event()
    watchdog_thread = None

    if health_path:
        try:
            write_bot_progress(health_path)
        except Exception:
            logger.warning("Failed to write bot progress heartbeat", exc_info=True)

        process_started_at = wall_time_fn()

        def watch_stale_cycle() -> None:
            while not watchdog_wait_fn(watchdog_stop, _WATCHDOG_POLL_SECONDS):
                health = get_bot_health(
                    health_path,
                    poll_interval,
                    now=wall_time_fn(),
                    stale_after_seconds=_STALE_CYCLE_SHUTDOWN_SECONDS,
                )
                now = wall_time_fn()
                snapshot_missing_or_invalid = health["status"] in {
                    "starting",
                    "degraded",
                }
                if health["status"] != "stale" and not (
                    snapshot_missing_or_invalid
                    and (now - process_started_at).total_seconds()
                    > _STALE_CYCLE_SHUTDOWN_SECONDS
                ):
                    continue

                stale_shutdown_requested.set()
                attempt_reader = getattr(loop, "active_publication_attempt", None)
                mark_unconfirmed = getattr(
                    loop, "mark_active_publication_unconfirmed", None
                )
                attempt = None
                if callable(attempt_reader):
                    try:
                        attempt = attempt_reader()
                    except Exception as exc:
                        logger.error(
                            "Stale worker could not inspect active publication",
                            extra={
                                "event": "stale_cycle_publication_inspection_failed",
                                "phase": "shutdown",
                                "operation": "read_active_publication",
                                "reason": "active_attempt_unavailable",
                                "error_type": type(exc).__name__,
                            },
                        )
                if attempt is not None and callable(mark_unconfirmed):
                    try:
                        confirmed = mark_unconfirmed(reason="worker heartbeat stale")
                    except Exception as exc:
                        logger.error(
                            "Stale worker could not persist unknown publication outcome",
                            extra={
                                "event": "stale_cycle_publication_unconfirmed_failed",
                                "phase": "shutdown",
                                "operation": "mark_publication_unconfirmed",
                                "reason": "database_transition_unconfirmed",
                                "error_type": type(exc).__name__,
                            },
                        )
                    else:
                        if confirmed:
                            logger.warning(
                                "Stale worker persisted unknown publication outcome",
                                extra={
                                    "event": "stale_cycle_publication_unconfirmed",
                                    "phase": "shutdown",
                                    "operation": "mark_publication_unconfirmed",
                                    "reason": "heartbeat_timeout",
                                },
                            )
                        else:
                            logger.warning(
                                "Stale worker active publication was already resolved",
                                extra={
                                    "event": "stale_cycle_publication_resolution_changed",
                                    "phase": "shutdown",
                                    "operation": "mark_publication_unconfirmed",
                                    "reason": "active_attempt_changed",
                                },
                            )
                elif attempt is not None:
                    logger.error(
                        "Stale worker cannot persist publication outcome",
                        extra={
                            "event": "stale_cycle_publication_unconfirmed_failed",
                            "phase": "shutdown",
                            "operation": "mark_publication_unconfirmed",
                            "reason": "transition_unavailable",
                        },
                    )

                logger.error(
                    "Worker heartbeat stayed stale beyond the recovery limit",
                    extra={
                        "event": "stale_cycle_worker_termination_started",
                        "phase": "shutdown",
                        "operation": "terminate_worker_process",
                        "reason": "heartbeat_timeout",
                        "stale_after_seconds": _STALE_CYCLE_SHUTDOWN_SECONDS,
                    },
                )
                try:
                    process_terminate_fn()
                except Exception as exc:
                    logger.critical(
                        "Stale worker process termination failed",
                        extra={
                            "event": "stale_cycle_worker_termination_failed",
                            "phase": "shutdown",
                            "operation": "terminate_worker_process",
                            "reason": "process_termination_failed",
                            "error_type": type(exc).__name__,
                        },
                    )
                return

        watchdog_thread = threading.Thread(
            target=watch_stale_cycle,
            name="bot-health-watchdog",
            daemon=True,
        )
        watchdog_thread.start()

    def stop_cycle_retry_for_active_publication() -> bool:
        attempt_reader = getattr(loop, "active_publication_attempt", None)
        if not callable(attempt_reader):
            return False
        try:
            attempt = attempt_reader()
        except Exception as exc:
            logger.error(
                "Cycle recovery stopped because the active publication is unknown",
                extra={
                    "event": "main_loop_cycle_retry_stopped",
                    "phase": "cycle",
                    "operation": "read_active_publication",
                    "reason": "active_attempt_unavailable",
                    "error_type": type(exc).__name__,
                },
            )
            return True
        if attempt is None:
            return False

        mark_unconfirmed = getattr(
            loop, "mark_active_publication_unconfirmed", None
        )
        if not callable(mark_unconfirmed):
            logger.error(
                "Cycle recovery stopped because publication safety cannot be persisted",
                extra={
                    "event": "main_loop_cycle_retry_stopped",
                    "phase": "publication",
                    "operation": "mark_publication_unconfirmed",
                    "reason": "transition_unavailable",
                },
            )
            return True
        try:
            confirmed = mark_unconfirmed(
                reason="cycle failed after durable submit"
            )
        except Exception as exc:
            logger.error(
                "Cycle recovery could not persist the unknown publication outcome",
                extra={
                    "event": "main_loop_cycle_retry_stopped",
                    "phase": "publication",
                    "operation": "mark_publication_unconfirmed",
                    "reason": "database_transition_unconfirmed",
                    "error_type": type(exc).__name__,
                },
            )
        else:
            logger.warning(
                "Cycle recovery stopped after a durable submit attempt",
                extra={
                    "event": "main_loop_cycle_retry_stopped",
                    "phase": "publication",
                    "operation": "mark_publication_unconfirmed",
                    "reason": (
                        "publication_outcome_unconfirmed"
                        if confirmed
                        else "active_attempt_changed"
                    ),
                },
            )
        return True

    try:
        while max_cycles is None or cycles < max_cycles:
            cycle_succeeded = True
            skip_cycle_this_iteration = skip_next_cycle
            if skip_cycle_this_iteration:
                skip_next_cycle = False
                cycle_failure_count = 0
                cycle_succeeded = False
                logger.warning(
                    "Comment-processing cycle skipped after bounded recovery was exhausted",
                    extra={
                        "event": "main_loop_operation_skipped",
                        "cycle": cycles + 1,
                        "phase": "cycle",
                        "operation": "run_cycle",
                        "reason": "recovery_cooldown",
                    },
                )
            elif health_path:
                try:
                    write_bot_progress(health_path)
                except Exception:
                    logger.warning("Failed to write bot progress heartbeat", exc_info=True)
            if not skip_cycle_this_iteration:
                try:
                    loop.run_cycle()
                except Exception as exc:
                    cycle_succeeded = False
                    active_publication = stop_cycle_retry_for_active_publication()
                    cycle_failure_count = (
                        0 if active_publication else cycle_failure_count + 1
                    )
                    logger.warning(
                        "Comment-processing cycle failed under bounded retry policy",
                        extra={
                            "event": "main_loop_cycle_failed",
                            "cycle": cycles + 1,
                            **_safe_error_fields(
                                exc, phase="cycle", operation="run_cycle"
                            ),
                            "retry_policy": "max_3_consecutive_failures",
                            "consecutive_failures": cycle_failure_count,
                        },
                    )
                    recover_if_browser_crashed = getattr(
                        session, "recover_if_browser_crashed", None
                    )
                    if callable(recover_if_browser_crashed) and _is_renderer_crash_error(
                        exc
                    ):
                        try:
                            recover_if_browser_crashed(exc)
                        except Exception as recovery_error:
                            logger.warning(
                                "Browser session recovery failed; preserving the cycle error",
                                extra={
                                    "event": "main_loop_browser_recovery_failed",
                                    "cycle": cycles + 1,
                                    **_safe_error_fields(
                                        exc,
                                        phase="cycle",
                                        operation="recover_browser_session",
                                    ),
                                    "recovery_error_type": type(recovery_error).__name__,
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
                            "Comment-processing cycle failed; supervisor recovery is bounded",
                            exc,
                        )
                        last_error_signature = error_signature
                        last_error_notification_at = now
                    if cycle_failure_count >= _MAX_OPERATION_ATTEMPTS:
                        logger.error(
                            "Comment-processing recovery exhausted its failure budget",
                            extra={
                                "event": "main_loop_recovery_exhausted",
                                "phase": "cycle",
                                "operation": "run_cycle",
                                "reason": "retry_limit_reached",
                                "attempts": cycle_failure_count,
                                "max_attempts": _MAX_OPERATION_ATTEMPTS,
                            },
                        )
                        skip_next_cycle = True
                        cycle_failure_count = 0
                else:
                    cycle_failure_count = 0

            if stale_shutdown_requested.is_set():
                raise SystemExit(0)
            if health_path:
                authenticated = False
                try:
                    is_logged_in = getattr(session, "is_logged_in", None)
                    if callable(is_logged_in):
                        authenticated = bool(is_logged_in())
                except Exception:
                    authenticated = False
                try:
                    write_bot_health(
                        health_path,
                        cycle_succeeded=cycle_succeeded,
                        authenticated=authenticated,
                        cycle_in_progress=False,
                    )
                except Exception:
                    logger.warning("Failed to write bot health snapshot", exc_info=True)

            now = time_fn()
            if now - last_keepalive >= keepalive_interval:
                if skip_next_keepalive:
                    skip_next_keepalive = False
                    keepalive_failure_count = 0
                    logger.warning(
                        "Keep-alive skipped after bounded recovery was exhausted",
                        extra={
                            "event": "main_loop_operation_skipped",
                            "phase": "keepalive",
                            "operation": "session.keep_alive",
                            "reason": "recovery_cooldown",
                        },
                    )
                else:
                    try:
                        session.keep_alive()
                    except Exception as exc:
                        keepalive_failure_count += 1
                        logger.warning(
                            "Keep-alive operation failed under bounded retry policy",
                            extra={
                                "event": "main_loop_keepalive_failed",
                                **_safe_error_fields(
                                    exc,
                                    phase="keepalive",
                                    operation="session.keep_alive",
                                ),
                                "retry_policy": "max_3_consecutive_failures",
                                "consecutive_failures": keepalive_failure_count,
                            },
                        )
                        recover_if_browser_crashed = getattr(
                            session, "recover_if_browser_crashed", None
                        )
                        if callable(recover_if_browser_crashed) and _is_renderer_crash_error(
                            exc
                        ):
                            try:
                                recover_if_browser_crashed(exc)
                            except Exception as recovery_error:
                                logger.warning(
                                    "Browser recovery failed after keep-alive error",
                                    extra={
                                        "event": "main_loop_keepalive_recovery_failed",
                                        **_safe_error_fields(
                                            exc,
                                            phase="keepalive",
                                            operation="recover_browser_session",
                                        ),
                                        "recovery_error_type": type(recovery_error).__name__,
                                    },
                                )
                        notifier.notify_error("Keep-alive failed", exc)
                        if keepalive_failure_count >= _MAX_OPERATION_ATTEMPTS:
                            logger.error(
                                "Keep-alive recovery exhausted its failure budget",
                                extra={
                                    "event": "main_loop_recovery_exhausted",
                                    "phase": "keepalive",
                                    "operation": "session.keep_alive",
                                    "reason": "retry_limit_reached",
                                    "attempts": keepalive_failure_count,
                                    "max_attempts": _MAX_OPERATION_ATTEMPTS,
                                },
                            )
                            skip_next_keepalive = True
                            keepalive_failure_count = 0
                    else:
                        keepalive_failure_count = 0
                last_keepalive = now

            sleep_fn(poll_interval)
            cycles += 1
    finally:
        watchdog_stop.set()
        if watchdog_thread is not None:
            watchdog_thread.join()


def main() -> None:
    configure_logging()
    settings = Settings()
    loop, session, notifier = build_app(settings)
    DzenLoginControlServer(
        settings.DZEN_LOGIN_CONTROL_SOCKET, session
    ).serve_in_thread()
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
