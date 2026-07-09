from __future__ import annotations

import time
from collections.abc import Callable

import httpx


class TelegramAuthAssistant:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        proxy_url: str,
        poll_timeout: float = 30.0,
        poll_interval: float = 1.0,
        client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.sleep_fn = sleep_fn
        self._client = client or self._make_client(
            proxy_url.strip() if proxy_url else ""
        )

    def ask_ready(self) -> bool:
        self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": (
                    "Дзену нужна авторизация. Готов сейчас войти? "
                    "Нажми «Готов», и я открою страницу входа."
                ),
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "Готов", "callback_data": "ready"}],
                    ],
                },
            },
        )

        return self._poll_until(self._matching_callback) is not None

    def relay_code_prompt(self, prompt_text: str) -> str:
        self._post("sendMessage", {"chat_id": self.chat_id, "text": prompt_text})

        update = self._poll_until(self._matching_text_message)
        if update is None:
            raise TimeoutError("Telegram auth code was not received in time")

        text = self._matching_text_message(update)
        if text is None:
            raise TimeoutError("Telegram auth code was not received in time")
        return text

    def _make_client(self, proxy_url: str) -> httpx.Client:
        if proxy_url:
            return httpx.Client(proxy=proxy_url)
        return httpx.Client()

    def _post(self, method: str, payload: dict) -> httpx.Response:
        response = self._client.post(self._url(method), json=payload)
        response.raise_for_status()
        return response

    def _poll_until(
        self,
        matcher: Callable[[dict], object | None],
    ) -> dict | None:
        deadline = time.monotonic() + self.poll_timeout
        offset: int | None = None
        first_attempt = True

        while first_attempt or time.monotonic() < deadline:
            first_attempt = False
            payload: dict[str, object] = {"timeout": self.poll_interval}
            if offset is not None:
                payload["offset"] = offset

            response = self._post("getUpdates", payload)
            updates = response.json().get("result", [])
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                if matcher(update) is not None:
                    return update

            if time.monotonic() >= deadline:
                break
            self.sleep_fn(self.poll_interval)

        return None

    def _matching_callback(self, update: dict) -> dict | None:
        callback = update.get("callback_query")
        if not isinstance(callback, dict):
            return None

        message = callback.get("message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat")
        if not isinstance(chat, dict) or str(chat.get("id")) != self.chat_id:
            return None

        if callback.get("data") != "ready":
            return None

        return callback

    def _matching_text_message(self, update: dict) -> str | None:
        message = update.get("message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat")
        if not isinstance(chat, dict) or str(chat.get("id")) != self.chat_id:
            return None

        text = message.get("text")
        return text if isinstance(text, str) else None

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"
