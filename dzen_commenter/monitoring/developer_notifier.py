from __future__ import annotations

import logging

from dzen_commenter.contracts.interfaces import Notifier


class DeveloperNotifier:
    """Guarded notification facade for developer-facing failures."""

    def __init__(self, transport: Notifier) -> None:
        self.transport = transport

    def notify(self, message: str) -> None:
        try:
            self.transport.notify(message)
        except Exception:
            logging.getLogger(__name__).warning(
                "Developer notification delivery failed",
                exc_info=True,
            )

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        try:
            self.transport.notify_error(message, error)
        except Exception:
            logging.getLogger(__name__).warning(
                "Developer error notification delivery failed",
                exc_info=True,
            )


class DeveloperNotificationHandler(logging.Handler):
    """Forward only ERROR and CRITICAL records without recursive logging."""

    def __init__(self, notifier: Notifier) -> None:
        super().__init__(level=logging.ERROR)
        self.notifier = notifier

    def emit(self, record: logging.LogRecord) -> None:
        error = record.exc_info[1] if record.exc_info else None
        self.notifier.notify_error(record.getMessage(), error)
