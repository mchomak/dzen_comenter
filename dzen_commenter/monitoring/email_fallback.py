from __future__ import annotations

import smtplib
from collections.abc import Callable
from email.message import EmailMessage


class EmailFallbackNotifier:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str,
        to_addrs: list[str],
        smtp_client_factory: Callable[..., object] = smtplib.SMTP,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.smtp_client_factory = smtp_client_factory

    def notify(self, message: str) -> None:
        self._send(message)

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        if error is not None:
            message = f"{message}\n{type(error).__name__}: {error}"
        self._send(message)

    def _send(self, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg["Subject"] = "Dzen Commenter notification"
        msg.set_content(body)

        client = self.smtp_client_factory(self.host, self.port)
        try:
            if self.user:
                client.starttls()
                client.login(self.user, self.password)
            client.sendmail(self.from_addr, self.to_addrs, msg.as_string())
        finally:
            quit_method = getattr(client, "quit", None)
            if callable(quit_method):
                quit_method()
