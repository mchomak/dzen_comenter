from __future__ import annotations

from importlib import import_module

from dzen_commenter.contracts.interfaces import Notifier


class TelegramNotifier:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        proxy_url: str,
        fallback: Notifier | None = None,
        client: object | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.proxy_url = proxy_url.strip() if proxy_url else ""
        self.fallback = fallback
        self._httpx = import_module("httpx")
        self._client = client or self._make_client()

    def notify(self, message: str) -> None:
        try:
            self._send(message)
        except self._httpx.HTTPError:
            if self.fallback is not None:
                self.fallback.notify(message)

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        text = self._format_error(message, error)
        try:
            self._send(text)
        except self._httpx.HTTPError:
            if self.fallback is not None:
                self.fallback.notify_error(message, error)

    def _make_client(self) -> object:
        if self.proxy_url:
            return self._httpx.Client(proxy=self.proxy_url)
        return self._httpx.Client()

    def _send(self, text: str) -> None:
        response = self._client.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text},
        )
        response.raise_for_status()

    def _format_error(self, message: str, error: Exception | None) -> str:
        if error is None:
            return message
        return f"{message}\n{type(error).__name__}: {error}"
