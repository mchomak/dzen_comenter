import inspect

from dzen_commenter.contracts import interfaces
from dzen_commenter.contracts.interfaces import AuthAssistant


def test_auth_assistant_exported():
    assert hasattr(interfaces, "AuthAssistant")


def test_auth_assistant_is_protocol():
    assert getattr(AuthAssistant, "_is_protocol", False) is True


def test_ask_ready_signature():
    sig = inspect.signature(AuthAssistant.ask_ready)
    params = [p for p in sig.parameters if p != "self"]
    assert params == []
    assert sig.return_annotation is bool


def test_notify_sms_restart_signature():
    sig = inspect.signature(AuthAssistant.notify_sms_restart)
    params = [p for p in sig.parameters if p != "self"]
    assert params == []
    assert sig.return_annotation is None


def test_notify_sms_pending_signature():
    sig = inspect.signature(AuthAssistant.notify_sms_pending)
    params = [p for p in sig.parameters if p != "self"]
    assert params == []
    assert sig.return_annotation is None


def test_relay_code_prompt_signature():
    sig = inspect.signature(AuthAssistant.relay_code_prompt)
    params = [p for p in sig.parameters if p != "self"]
    assert params == ["prompt_text"]
    assert sig.return_annotation is str
