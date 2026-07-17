from __future__ import annotations

from importlib import import_module
import logging

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
        except Exception:
            if self.fallback is not None:
                try:
                    self.fallback.notify(message)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Telegram and email notification delivery failed",
                        exc_info=True,
                    )

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        text = self._format_error(message, error)
        try:
            self._send(text)
        except Exception:
            if self.fallback is not None:
                try:
                    self.fallback.notify_error(message, error)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Telegram and email error notification delivery failed",
                        exc_info=True,
                    )

    def _make_client(self) -> object:
        if self.proxy_url:
            return self._httpx.Client(proxy=self.proxy_url)
        return self._httpx.Client()

    def _send(self, text: str) -> None:
        chat_ids = [chat_id.strip() for chat_id in self.chat_id.split(",") if chat_id.strip()]
        if not chat_ids:
            raise ValueError("No Telegram chat IDs configured")

        errors = []
        for chat_id in chat_ids:
            try:
                response = self._client.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
                response.raise_for_status()
            except Exception as error:
                errors.append(error)

        if len(errors) == len(chat_ids):
            raise errors[0]

    def _format_error(self, message: str, error: Exception | None) -> str:
        if error is None:
            return message
        return f"{message}\n{type(error).__name__}: {error}"
