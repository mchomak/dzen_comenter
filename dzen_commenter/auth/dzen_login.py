from __future__ import annotations

import re
from typing import Any

from dzen_commenter.dzen import selectors


class DzenLoginAuthenticator:
    def __init__(
        self,
        page: Any,
        *,
        comments_url: str,
        phone: str,
        password: str,
        timeout_ms: int = 30000,
    ) -> None:
        self._page = page
        self._comments_url = comments_url
        self._phone = phone
        self._password = password
        self._timeout_ms = max(1, timeout_ms)
        self._short_timeout_ms = min(5000, self._timeout_ms)

    def login(self) -> bool:
        if not self._phone or not self._password:
            return False

        self._page.goto(self._comments_url)
        self._click_optional(selectors.LOGIN_BUTTON, self._short_timeout_ms)

        phone_input = self._require_visible(
            selectors.LOGIN_PHONE_INPUT,
            "Dzen phone input",
            self._timeout_ms,
        )
        self._call(phone_input.fill, self._phone, timeout_ms=self._short_timeout_ms)
        self._ensure_no_captcha()
        self._click_required(
            selectors.LOGIN_PHONE_CONTINUE,
            "Dzen phone continue button",
            self._timeout_ms,
        )

        self._wait_after_transition()
        self._ensure_no_captcha()

        password_input = self._first_visible(
            selectors.VK_PASSWORD_INPUT,
            self._short_timeout_ms,
        )
        if password_input is None:
            self._click_optional(selectors.VK_PASSWORD_METHOD, self._short_timeout_ms)
            password_input = self._require_visible(
                selectors.VK_PASSWORD_INPUT,
                "VK ID password input",
                self._timeout_ms,
            )

        self._call(password_input.fill, self._password, timeout_ms=self._short_timeout_ms)
        self._ensure_no_captcha()
        self._click_required(
            selectors.VK_PASSWORD_SUBMIT,
            "VK ID password submit button",
            self._timeout_ms,
        )

        self._wait_after_transition()
        self._ensure_no_captcha()
        if not self._is_on_dzen():
            self._click_optional(selectors.VK_ALLOW_ACCESS, self._short_timeout_ms)
        self._wait_for_dzen_redirect()
        return True

    def _first_visible(self, selector: str, timeout_ms: int) -> Any | None:
        locator = self._page.locator(selector).first()
        try:
            self._call(locator.wait_for, state="visible", timeout_ms=timeout_ms)
        except Exception:
            return None
        return locator

    def _require_visible(self, selector: str, name: str, timeout_ms: int) -> Any:
        locator = self._first_visible(selector, timeout_ms)
        if locator is None:
            raise RuntimeError(f"{name} was not found during Dzen login")
        return locator

    def _click_optional(self, selector: str, timeout_ms: int) -> bool:
        locator = self._first_visible(selector, timeout_ms)
        if locator is None:
            return False
        self._call(locator.click, timeout_ms=timeout_ms)
        return True

    def _click_required(self, selector: str, name: str, timeout_ms: int) -> None:
        locator = self._require_visible(selector, name, timeout_ms)
        self._call(locator.click, timeout_ms=timeout_ms)

    def _ensure_no_captcha(self) -> None:
        if self._first_visible(selectors.AUTH_CAPTCHA, 1000) is not None:
            raise RuntimeError("Dzen login requires captcha or additional verification")

    def _wait_after_transition(self) -> None:
        try:
            self._call(
                self._page.wait_for_load_state,
                "domcontentloaded",
                timeout_ms=self._short_timeout_ms,
            )
        except Exception:
            pass

    def _wait_for_dzen_redirect(self) -> None:
        try:
            self._call(
                self._page.wait_for_url,
                re.compile(r"https://dzen\.ru/.*"),
                timeout_ms=self._timeout_ms,
            )
        except Exception:
            pass
        self._wait_after_transition()

    def _is_on_dzen(self) -> bool:
        url = getattr(self._page, "url", "")
        return isinstance(url, str) and "://dzen.ru" in url

    @staticmethod
    def _call(method: Any, *args: Any, timeout_ms: int | None = None, **kwargs: Any) -> Any:
        if timeout_ms is not None:
            kwargs["timeout"] = timeout_ms
        try:
            return method(*args, **kwargs)
        except TypeError:
            if "timeout" not in kwargs:
                raise
            kwargs.pop("timeout")
            return method(*args, **kwargs)
