import ast
import inspect
from pathlib import Path

import httpx

from dzen_commenter.monitoring import EmailFallbackNotifier, TelegramNotifier

TOKEN = "TOKEN"
CHAT_ID = "12345"
PROXY_URL = "socks5://proxy.example.test:1080"


class FallbackSpy:
    def __init__(self):
        self.notify_calls = []
        self.notify_error_calls = []

    def notify(self, message):
        self.notify_calls.append(message)

    def notify_error(self, message, error=None):
        self.notify_error_calls.append((message, error))


def test_imports_and_contract_signatures():
    assert TelegramNotifier is not None
    assert EmailFallbackNotifier is not None

    for cls in (TelegramNotifier, EmailFallbackNotifier):
        notify_sig = inspect.signature(cls.notify)
        assert list(notify_sig.parameters) == ["self", "message"]

        error_sig = inspect.signature(cls.notify_error)
        assert list(error_sig.parameters) == ["self", "message", "error"]
        assert error_sig.parameters["error"].default is None


def test_notify_posts_send_message_payload():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = TelegramNotifier(
        bot_token=TOKEN,
        chat_id=CHAT_ID,
        proxy_url=PROXY_URL,
        client=client,
    )

    notifier.notify("hello")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith(f"/bot{TOKEN}/sendMessage")
    assert request.read() == b'{"chat_id":"12345","text":"hello"}'


def test_default_client_is_constructed_with_proxy(monkeypatch):
    captured = {}

    class FakeHTTPX:
        HTTPError = httpx.HTTPError

        class Client:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def post(self, *args, **kwargs):
                return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        "dzen_commenter.monitoring.telegram_notifier.import_module",
        lambda name: FakeHTTPX,
    )

    TelegramNotifier(
        bot_token=TOKEN,
        chat_id=CHAT_ID,
        proxy_url=PROXY_URL,
    )

    assert captured == {"proxy": PROXY_URL}


def test_notify_uses_fallback_on_httpx_error():
    fallback = FallbackSpy()

    def handler(request):
        raise httpx.ConnectError("proxy unavailable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = TelegramNotifier(
        bot_token=TOKEN,
        chat_id=CHAT_ID,
        proxy_url=PROXY_URL,
        fallback=fallback,
        client=client,
    )

    notifier.notify("hello")

    assert fallback.notify_calls == ["hello"]


def test_notify_error_includes_exception_text_and_falls_back():
    requests = []
    fallback = FallbackSpy()

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"ok": True})
        raise httpx.ConnectError("proxy unavailable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = TelegramNotifier(
        bot_token=TOKEN,
        chat_id=CHAT_ID,
        proxy_url=PROXY_URL,
        fallback=fallback,
        client=client,
    )
    error = RuntimeError("boom")

    notifier.notify_error("broken", error)
    notifier.notify_error("broken", error)

    assert b"RuntimeError" in requests[0].read()
    assert b"boom" in requests[0].read()
    assert fallback.notify_error_calls == [("broken", error)]


def test_new_monitoring_dependency_imports_are_clean():
    allowed_roots = {
        "__future__",
        "collections",
        "email",
        "importlib",
        "smtplib",
        "dzen_commenter",
    }
    allowed_modules = {
        "dzen_commenter.contracts.interfaces",
    }
    files = [
        Path("dzen_commenter/monitoring/telegram_notifier.py"),
        Path("dzen_commenter/monitoring/email_fallback.py"),
    ]

    for py_file in files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)

        for module in modules:
            root = module.split(".")[0]
            assert root in allowed_roots, f"{py_file.name} imports {module}"
            if root == "dzen_commenter":
                assert module in allowed_modules, f"{py_file.name} imports {module}"
