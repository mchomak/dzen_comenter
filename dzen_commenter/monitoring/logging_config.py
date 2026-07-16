import json
import logging
from dzen_commenter.contracts.interfaces import Notifier
from dzen_commenter.monitoring.developer_notifier import DeveloperNotificationHandler
from datetime import datetime, timezone

# Marker attribute set on the handler added by this module, so configure_logging
# can be idempotent (remove a previously added handler before adding a new one).
_HANDLER_MARKER = "_dzen_commenter_monitoring_handler"
_NOTIFICATION_HANDLER_MARKER = "_dzen_commenter_notification_handler"

# stdlib LogRecord attributes that are NOT structural extras; everything else
# present on a record's __dict__ was passed via extra={...} and should be emitted.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime"}


class StructuredFormatter(logging.Formatter):
    """Format a LogRecord as a single machine-readable JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Arbitrary structural fields passed via extra={...}.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS:
                payload[key] = value

        if record.exc_info:
            exc_type = record.exc_info[0]
            payload["exception"] = {
                "type": exc_type.__name__ if exc_type else None,
                "traceback": self.formatException(record.exc_info),
            }

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO", notifier: Notifier | None = None
) -> None:
    """Install a single StructuredFormatter StreamHandler on the root logger.

    Idempotent: a handler previously added by this module is removed before a
    new one is added, so repeated calls do not multiply handlers.
    """
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
        if getattr(handler, _NOTIFICATION_HANDLER_MARKER, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)

    if notifier is not None:
        notification_handler = DeveloperNotificationHandler(notifier)
        setattr(notification_handler, _NOTIFICATION_HANDLER_MARKER, True)
        root.addHandler(notification_handler)
