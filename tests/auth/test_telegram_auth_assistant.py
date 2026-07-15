import ast
import inspect
from pathlib import Path

import httpx
import pytest

from dzen_commenter.auth import TelegramAuthAssistant

TOKEN = "TOKEN"
CHAT_ID = "12345"
PROXY_URL = "socks5://proxy.example.test:1080"


def _json_response(payload):
    return httpx.Response(200, json=payload)


class RequestRecorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return _json_response({"ok": True, "result": []})


def _assistant(recorder, **overrides):
    sleep_calls = []
    values = {
        "bot_token": TOKEN,
        "chat_id": CHAT_ID,
        "proxy_url": PROXY_URL,
        "poll_timeout": 0,
        "poll_interval": 0.1,
        "client": httpx.Client(transport=httpx.MockTransport(recorder)),
        "sleep_fn": sleep_calls.append,
    }
    values.update(overrides)
    return TelegramAuthAssistant(**values), sleep_calls


def test_import_and_contract_signatures():
    ready_sig = inspect.signature(TelegramAuthAssistant.ask_ready)
    assert list(ready_sig.parameters) == ["self"]
    assert ready_sig.return_annotation in ("bool", bool)

    code_sig = inspect.signature(TelegramAuthAssistant.relay_code_prompt)
    assert list(code_sig.parameters) == ["self", "prompt_text"]
    assert code_sig.return_annotation in ("str", str)

    restart_sig = inspect.signature(TelegramAuthAssistant.notify_sms_restart)
    assert list(restart_sig.parameters) == ["self"]
    assert restart_sig.return_annotation in ("None", None)

    pending_sig = inspect.signature(TelegramAuthAssistant.notify_sms_pending)
    assert list(pending_sig.parameters) == ["self"]
    assert pending_sig.return_annotation in ("None", None)


def test_ask_ready_sends_inline_button_and_returns_true_on_callback():
    callback_update = {
        "update_id": 10,
        "callback_query": {
            "id": "cb-1",
            "message": {"chat": {"id": int(CHAT_ID)}},
            "data": "ready",
        },
    }
    recorder = RequestRecorder(
        [
            _json_response({"ok": True, "result": {"message_id": 1}}),
            _json_response({"ok": True, "result": [callback_update]}),
        ]
    )
    assistant, _ = _assistant(recorder)

    assert assistant.ask_ready() is True

    send_request = recorder.requests[0]
    assert send_request.url.path.endswith(f"/bot{TOKEN}/sendMessage")
    send_payload = send_request.read()
    assert b'"chat_id":"12345"' in send_payload
    assert b'"reply_markup"' in send_payload
    assert (
        "Дзену нужна авторизация. Готов сейчас войти?".encode("utf-8")
        in send_payload
    )
    assert "Готов".encode("utf-8") in send_payload
    assert b"????" not in send_payload

    status_payload = recorder.requests[2].read()
    assert (
        "Авторизация начата. Открываю страницу входа, подожди немного.".encode(
            "utf-8"
        )
        in status_payload
    )


def test_ask_ready_ignores_non_ready_callback():
    callback_update = {
        "update_id": 10,
        "callback_query": {
            "id": "cb-1",
            "message": {"chat": {"id": int(CHAT_ID)}},
            "data": "other",
        },
    }
    recorder = RequestRecorder(
        [
            _json_response({"ok": True, "result": {"message_id": 1}}),
            _json_response({"ok": True, "result": [callback_update]}),
        ]
    )
    assistant, _ = _assistant(recorder)

    assert assistant.ask_ready() is False


def test_default_client_is_constructed_without_proxy_when_empty(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "dzen_commenter.auth.telegram_auth_assistant.httpx.Client",
        FakeClient,
    )

    TelegramAuthAssistant(
        bot_token=TOKEN,
        chat_id=CHAT_ID,
        proxy_url="   ",
    )

    assert captured == {}


def test_ask_ready_returns_false_on_timeout_without_real_wait():
    recorder = RequestRecorder(
        [
            _json_response({"ok": True, "result": {"message_id": 1}}),
            _json_response({"ok": True, "result": []}),
        ]
    )
    assistant, sleep_calls = _assistant(recorder)

    assert assistant.ask_ready() is False
    assert sleep_calls == []


def test_ask_ready_does_not_send_duplicate_prompts_before_confirmation():
    recorder = RequestRecorder(
        [
            _json_response({"ok": True, "result": {"message_id": 1}}),
            _json_response({"ok": True, "result": []}),
            _json_response({"ok": True, "result": []}),
        ]
    )
    assistant, _ = _assistant(recorder)

    assert assistant.ask_ready() is False
    assert assistant.ask_ready() is False
    assert [request.url.path.split("/")[-1] for request in recorder.requests] == [
        "sendMessage",
        "getUpdates",
        "getUpdates",
    ]


def test_poll_auth_command_returns_true_for_configured_chat():
    update = {
        "update_id": 10,
        "message": {"chat": {"id": int(CHAT_ID)}, "text": "/auth"},
    }
    recorder = RequestRecorder([_json_response({"ok": True, "result": [update]})])
    assistant, _ = _assistant(recorder)

    assert assistant.poll_auth_command() is True


@pytest.mark.parametrize("text", ["/auth extra", "/auth@", "/auth@bot extra"])
def test_poll_auth_command_rejects_extra_text(text):
    update = {
        "update_id": 10,
        "message": {"chat": {"id": int(CHAT_ID)}, "text": text},
    }
    recorder = RequestRecorder([_json_response({"ok": True, "result": [update]})])
    assistant, _ = _assistant(recorder)

    assert assistant.poll_auth_command() is False


def test_poll_auth_command_ignores_other_chat_and_accepts_bot_suffix():
    updates = [
        {
            "update_id": 10,
            "message": {"chat": {"id": 999}, "text": "/auth"},
        },
        {
            "update_id": 11,
            "message": {"chat": {"id": int(CHAT_ID)}, "text": "/auth@my_bot"},
        },
    ]
    recorder = RequestRecorder([_json_response({"ok": True, "result": updates})])
    assistant, _ = _assistant(recorder)

    assert assistant.poll_auth_command() is True


def test_reset_ready_prompt_allows_sending_prompt_again():
    recorder = RequestRecorder(
        [
            _json_response({"ok": True, "result": {"message_id": 1}}),
            _json_response({"ok": True, "result": []}),
            _json_response({"ok": True, "result": {"message_id": 2}}),
            _json_response({"ok": True, "result": []}),
        ]
    )
    assistant, _ = _assistant(recorder)

    assert assistant.ask_ready() is False
    assistant.reset_ready_prompt()
    assert assistant.ask_ready() is False
    assert [request.url.path.split("/")[-1] for request in recorder.requests] == [
        "sendMessage",
        "getUpdates",
        "sendMessage",
        "getUpdates",
    ]


def test_relay_code_prompt_returns_next_text_message():
    message_update = {
        "update_id": 11,
        "message": {"chat": {"id": int(CHAT_ID)}, "text": "482913"},
    }
    recorder = RequestRecorder(
        [
            _json_response({"ok": True, "result": {"message_id": 1}}),
            _json_response({"ok": True, "result": [message_update]}),
        ]
    )
    assistant, _ = _assistant(recorder)
    prompt = "Дзен запросил код подтверждения. Пришли код ответом в этот чат."

    assert assistant.relay_code_prompt(prompt) == "482913"
    send_payload = recorder.requests[0].read()
    assert prompt.encode("utf-8") in send_payload
    assert b"????" not in send_payload

    status_payload = recorder.requests[2].read()
    assert "Код принят. Продолжаю авторизацию.".encode("utf-8") in status_payload


def test_relay_code_prompt_raises_timeout_without_text_message():
    recorder = RequestRecorder(
        [
            _json_response({"ok": True, "result": {"message_id": 1}}),
            _json_response({"ok": True, "result": []}),
        ]
    )
    assistant, _ = _assistant(recorder)

    with pytest.raises(TimeoutError):
        assistant.relay_code_prompt("Enter code")


def test_notify_sms_restart_sends_message():
    recorder = RequestRecorder([_json_response({"ok": True, "result": {}})])
    assistant, _ = _assistant(recorder)

    assistant.notify_sms_restart()

    assert recorder.requests[0].url.path.endswith(f"/bot{TOKEN}/sendMessage")
    assert "SMS".encode("utf-8") in recorder.requests[0].read()


def test_notify_sms_pending_sends_message():
    recorder = RequestRecorder([_json_response({"ok": True, "result": {}})])
    assistant, _ = _assistant(recorder)

    assistant.notify_sms_pending()

    assert recorder.requests[0].url.path.endswith(f"/bot{TOKEN}/sendMessage")
    assert "SMS".encode("utf-8") in recorder.requests[0].read()


def test_auth_layer_dependency_imports_are_clean():
    pkg_dir = Path("dzen_commenter/auth")
    forbidden = {
        "dzen_commenter.config",
        "requests",
        "yaml",
        "pydantic",
        "sqlalchemy",
        "playwright",
    }

    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        assert forbidden.isdisjoint(modules)
