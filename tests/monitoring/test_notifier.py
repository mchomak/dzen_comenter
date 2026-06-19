import inspect
import logging

from dzen_commenter.monitoring import LoggingNotifier
from dzen_commenter.monitoring.notifier import ALERT_LOGGER_NAME


# Acceptance 1: экспорт и реализация контракта (сигнатуры notify / notify_error).
def test_signatures_match_contract():
    notify_sig = inspect.signature(LoggingNotifier.notify)
    params = list(notify_sig.parameters)
    assert params == ["self", "message"]

    err_sig = inspect.signature(LoggingNotifier.notify_error)
    err_params = err_sig.parameters
    assert list(err_params) == ["self", "message", "error"]
    assert err_params["error"].default is None


def test_methods_callable_by_contract_signature():
    notifier = LoggingNotifier()
    notifier.notify("ok")
    notifier.notify_error("oops")
    notifier.notify_error("oops", ValueError("x"))


# Acceptance 2: notify эмитит структурную запись уведомления.
def test_notify_emits_notification_record(caplog):
    notifier = LoggingNotifier()
    with caplog.at_level(logging.INFO, logger=ALERT_LOGGER_NAME):
        notifier.notify("привет")
    records = [r for r in caplog.records if r.name == ALERT_LOGGER_NAME]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.INFO
    assert "привет" in record.getMessage()
    assert getattr(record, "notification", None) is True
    assert getattr(record, "event", None) == "notify"


# Acceptance 3: notify_error срабатывает на смоделированном сбое.
def test_notify_error_on_simulated_failure(caplog):
    notifier = LoggingNotifier()
    with caplog.at_level(logging.ERROR, logger=ALERT_LOGGER_NAME):
        try:
            raise RuntimeError("login failed")
        except RuntimeError as e:
            notifier.notify_error("сбой авторизации", e)
    records = [r for r in caplog.records if r.name == ALERT_LOGGER_NAME]
    assert len(records) == 1
    record = records[0]
    assert record.levelno >= logging.ERROR
    assert "сбой авторизации" in record.getMessage()
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError


def test_notify_error_without_error_still_emits(caplog):
    notifier = LoggingNotifier()
    with caplog.at_level(logging.ERROR, logger=ALERT_LOGGER_NAME):
        notifier.notify_error("msg")
    records = [r for r in caplog.records if r.name == ALERT_LOGGER_NAME]
    assert len(records) == 1
    assert records[0].levelno >= logging.ERROR
