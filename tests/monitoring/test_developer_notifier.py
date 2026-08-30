import logging

from dzen_commenter.monitoring.logging_config import configure_logging
from dzen_commenter.monitoring.developer_notifier import (
    DeveloperNotifier,
    DeveloperNotificationHandler,
)


class SpyTransport:
    def __init__(self, fail=False):
        self.fail = fail
        self.errors = []

    def notify_error(self, message, error=None):
        if self.fail:
            raise RuntimeError("smtp unavailable")
        self.errors.append((message, error))


def test_error_handler_forwards_error_and_critical_but_ignores_warning():
    transport = SpyTransport()
    logger = logging.getLogger("test.developer-alerts")
    handler = DeveloperNotificationHandler(DeveloperNotifier(transport))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("not critical")
        logger.error("site unavailable")
        logger.critical("bot is broken")
    finally:
        logger.removeHandler(handler)

    assert [message for message, _ in transport.errors] == [
        "site unavailable",
        "bot is broken",
    ]


def test_error_handler_contains_transport_failures():
    transport = SpyTransport(fail=True)
    handler = DeveloperNotificationHandler(DeveloperNotifier(transport))
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="smtp failure",
        args=(),
        exc_info=None,
    )

    handler.emit(record)


def test_developer_notifier_uses_developer_transport():
    transport = SpyTransport()
    notifier = DeveloperNotifier(transport)
    error = ValueError("down")

    notifier.notify_error("database unavailable", error)

    assert transport.errors == [("database unavailable", error)]


def test_developer_notifier_throttles_duplicate_errors(tmp_path):
    transport = SpyTransport()
    now = [100.0]
    notifier = DeveloperNotifier(
        transport,
        error_cooldown_provider=lambda: 3600,
        cooldown_state_path=str(tmp_path / "cooldown.json"),
        time_fn=lambda: now[0],
    )

    notifier.notify_error("database unavailable", ValueError("down"))
    notifier.notify_error("database unavailable", ValueError("down"))

    assert len(transport.errors) == 1


def test_developer_notifier_keeps_cooldown_across_restart(tmp_path):
    state_path = tmp_path / "cooldown.json"
    first_transport = SpyTransport()
    first = DeveloperNotifier(
        first_transport,
        error_cooldown_provider=lambda: 6 * 3600,
        cooldown_state_path=str(state_path),
        time_fn=lambda: 100.0,
    )
    first.notify_error("batch generation failed", RuntimeError("provider unavailable"))

    second_transport = SpyTransport()
    second = DeveloperNotifier(
        second_transport,
        error_cooldown_provider=lambda: 6 * 3600,
        cooldown_state_path=str(state_path),
        time_fn=lambda: 101.0,
    )
    second.notify_error("batch generation failed", RuntimeError("provider unavailable"))

    assert len(first_transport.errors) == 1
    assert second_transport.errors == []


def test_configure_logging_does_not_duplicate_developer_handler():
    transport = SpyTransport()
    root = logging.getLogger()
    configure_logging(notifier=DeveloperNotifier(transport))
    configure_logging(notifier=DeveloperNotifier(transport))

    handlers = [
        handler
        for handler in root.handlers
        if handler.__class__.__name__ == "DeveloperNotificationHandler"
    ]

    assert len(handlers) == 1
