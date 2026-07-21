import inspect
from email import message_from_string

from dzen_commenter.monitoring import EmailFallbackNotifier


class FakeSMTPClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.starttls_calls = 0
        self.login_calls = []
        self.sendmail_calls = []
        self.quit_calls = 0

    def login(self, user, password):
        self.login_calls.append((user, password))

    def starttls(self):
        self.starttls_calls += 1

    def sendmail(self, from_addr, to_addrs, body):
        self.sendmail_calls.append((from_addr, to_addrs, body))

    def quit(self):
        self.quit_calls += 1


class SMTPFactory:
    def __init__(self):
        self.clients = []

    def __call__(self, host, port):
        client = FakeSMTPClient(host, port)
        self.clients.append(client)
        return client


def _notifier(factory):
    return EmailFallbackNotifier(
        host="smtp.example.test",
        port=587,
        user="user",
        password="secret",
        from_addr="from@example.test",
        to_addrs=["ops@example.test", "owner@example.test"],
        smtp_client_factory=factory,
    )


def test_email_notifier_contract_signatures():
    notify_sig = inspect.signature(EmailFallbackNotifier.notify)
    assert list(notify_sig.parameters) == ["self", "message"]

    error_sig = inspect.signature(EmailFallbackNotifier.notify_error)
    assert list(error_sig.parameters) == ["self", "message", "error"]
    assert error_sig.parameters["error"].default is None


def test_notify_sends_email_with_expected_headers_and_body():
    factory = SMTPFactory()
    notifier = _notifier(factory)

    notifier.notify("msg")

    client = factory.clients[0]
    assert client.host == "smtp.example.test"
    assert client.port == 587
    assert client.starttls_calls == 1
    assert client.login_calls == [("user", "secret")]
    from_addr, to_addrs, body = client.sendmail_calls[0]
    parsed = message_from_string(body)
    assert from_addr == "from@example.test"
    assert to_addrs == ["ops@example.test", "owner@example.test"]
    assert parsed["From"] == "from@example.test"
    assert parsed["To"] == "ops@example.test, owner@example.test"
    assert "msg" in parsed.get_payload()
    assert client.quit_calls == 1


def test_to_addrs_read_from_provider_at_send_time():
    factory = SMTPFactory()
    holder = {"addrs": ["a@example.test"]}
    notifier = EmailFallbackNotifier(
        host="smtp.example.test",
        port=587,
        user="user",
        password="secret",
        from_addr="from@example.test",
        to_addrs=["stale@example.test"],
        smtp_client_factory=factory,
        to_addrs_provider=lambda: holder["addrs"],
    )

    notifier.notify("first")
    _, to_addrs, body = factory.clients[0].sendmail_calls[0]
    assert to_addrs == ["a@example.test"]
    assert message_from_string(body)["To"] == "a@example.test"

    # Edit recipients live — the next send must use the fresh value.
    holder["addrs"] = ["b@example.test", "c@example.test"]
    notifier.notify("second")
    assert factory.clients[1].sendmail_calls[0][1] == [
        "b@example.test",
        "c@example.test",
    ]


def test_notify_error_body_includes_exception_type_and_text():
    factory = SMTPFactory()
    notifier = _notifier(factory)

    notifier.notify_error("msg", ValueError("bad value"))

    body = factory.clients[0].sendmail_calls[0][2]
    assert "msg" in body
    assert "ValueError" in body
    assert "bad value" in body
