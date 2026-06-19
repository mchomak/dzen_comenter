import logging

ALERT_LOGGER_NAME = "dzen_commenter.alert"

# Structural marker that deterministically tags a record as a notification,
# so the alert channel's records are distinguishable from ordinary logs.
_NOTIFICATION_MARKER = {"notification": True}


class LoggingNotifier:
    """Notifier implementation that emits notifications as structured log records."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(ALERT_LOGGER_NAME)

    def notify(self, message: str) -> None:
        self._logger.info(message, extra={**_NOTIFICATION_MARKER, "event": "notify"})

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        self._logger.error(
            message,
            exc_info=error,
            extra={**_NOTIFICATION_MARKER, "event": "alert"},
        )
