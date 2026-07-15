from __future__ import annotations

import re
import time
from typing import Any

from dzen_commenter.contracts.interfaces import AuthAssistant
from dzen_commenter.dzen import selectors


class DzenSmsRestartRequested(RuntimeError):
    """Restart the login once so Yandex sends a fresh verification code."""


class DzenLoginAuthenticator:
    def __init__(
        self,
        page: Any,
        *,
        comments_url: str,
        phone: str,
        password: str,
        auth_assistant: AuthAssistant | None = None,
        timeout_ms: int = 30000,
        restart_on_sms: bool = False,
    ) -> None:
        self._page = page
        self._comments_url = comments_url
        self._phone = phone
        self._password = password
        self._auth_assistant = auth_assistant
        self._restart_on_sms = restart_on_sms
        self._timeout_ms = max(1, timeout_ms)
        self._short_timeout_ms = min(5000, self._timeout_ms)

    def login(self) -> bool:
        if not self._phone or not self._password:
            return False

        self._page.goto(self._comments_url)
        self._click_optional(selectors.LOGIN_BUTTON, self._short_timeout_ms)

        if self._is_email_login():
            self._click_required(
                selectors.YANDEX_ID_LOGIN,
                "Dzen Yandex ID login button",
                self._timeout_ms,
            )
            self._wait_after_transition()
            self._ensure_no_captcha()
            self._fill_yandex_login_if_visible()
        else:
            phone_input = self._require_visible(
                selectors.LOGIN_PHONE_INPUT,
                "Dzen phone input",
                self._timeout_ms,
            )
            self._call(phone_input.fill, self._phone, timeout_ms=self._short_timeout_ms)
            self._ensure_no_captcha()

            if self._click_optional(selectors.YANDEX_ID_LOGIN, self._short_timeout_ms):
                self._wait_after_transition()
                self._ensure_no_captcha()
                self._fill_yandex_login_if_visible()
            else:
                self._click_required(
                    selectors.LOGIN_PHONE_CONTINUE,
                    "Dzen phone continue button",
                    self._timeout_ms,
                )

        self._wait_after_transition()
        self._ensure_no_captcha()
        handled_code = self._handle_manual_code_if_visible()
        selected_account = self._select_yandex_account_if_visible()
        if self._dismiss_webauthn_promo_if_visible():
            self._wait_for_dzen_redirect()
            return True

        password_input = self._first_visible(
            selectors.VK_PASSWORD_INPUT,
            self._short_timeout_ms,
        )
        if password_input is None:
            if (handled_code or selected_account) and self._is_on_dzen():
                self._wait_for_dzen_redirect()
                return True
            if self._fill_yandex_login_if_visible():
                handled_code = self._handle_manual_code_if_visible()
                selected_account = self._select_yandex_account_if_visible()
                password_input = self._first_visible(
                    selectors.VK_PASSWORD_INPUT,
                    self._short_timeout_ms,
                )
                if password_input is None and (
                    handled_code or selected_account
                ) and self._is_on_dzen():
                    self._wait_for_dzen_redirect()
                    return True
            self._click_optional(selectors.VK_PASSWORD_METHOD, self._short_timeout_ms)
            if password_input is None:
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
        self._handle_manual_code_if_visible()
        self._select_yandex_account_if_visible()
        if not self._is_on_dzen():
            self._click_optional(selectors.VK_ALLOW_ACCESS, self._short_timeout_ms)
            self._select_yandex_account_if_visible()
        self._wait_for_dzen_redirect()
        return True

    def _fill_yandex_login_if_visible(self) -> bool:
        if self._is_account_choice_visible():
            return False
        if self._is_email_login():
            return self._fill_yandex_email_login()
        if self._is_yandex_phone_login_visible() is False:
            if self._first_visible(selectors.AUTH_CODE_INPUT, 500) is not None:
                return False

        login_input = self._first_visible(
            selectors.YANDEX_ID_LOGIN_INPUT,
            self._timeout_ms,
        )
        if login_input is None:
            return False

        self._click_optional(selectors.YANDEX_ID_PHONE_TAB, self._short_timeout_ms)
        login_input = self._require_visible(
            selectors.YANDEX_ID_LOGIN_INPUT,
            "Yandex ID login input",
            self._timeout_ms,
        )
        self._call(
            login_input.fill,
            self._yandex_login_phone(),
            timeout_ms=self._short_timeout_ms,
        )
        self._ensure_no_captcha()
        self._click_required(
            selectors.YANDEX_ID_CONTINUE,
            "Yandex ID continue button",
            self._timeout_ms,
        )
        self._wait_after_transition()
        self._wait_for_yandex_phone_login_to_advance()
        self._ensure_no_captcha()
        return True

    def _fill_yandex_email_login(self) -> bool:
        username_input = self._first_visible(
            selectors.YANDEX_ID_USERNAME_INPUT,
            500,
        )
        if username_input is None:
            self._click_required(
                selectors.YANDEX_ID_MORE_BUTTON,
                "Yandex ID More button",
                self._timeout_ms,
            )
            self._wait_after_transition()
            self._click_first_required(
                selectors.YANDEX_ID_USERNAME_LOGIN_SELECTORS,
                "Yandex ID username login button",
                self._timeout_ms,
            )
            self._wait_after_transition()
            username_input = self._require_visible(
                selectors.YANDEX_ID_USERNAME_INPUT,
                "Yandex ID username input",
                self._timeout_ms,
            )

        self._call(
            username_input.fill,
            self._phone.strip(),
            timeout_ms=self._short_timeout_ms,
        )
        self._ensure_no_captcha()
        self._click_required(
            selectors.YANDEX_ID_CONTINUE,
            "Yandex ID continue button",
            self._timeout_ms,
        )
        self._wait_after_transition()
        self._ensure_no_captcha()
        return True

    def _first_visible(self, selector: str, timeout_ms: int) -> Any | None:
        locator_group = self._page.locator(selector)
        if hasattr(locator_group, "count") and hasattr(locator_group, "nth"):
            deadline = time.monotonic() + timeout_ms / 1000
            while time.monotonic() < deadline:
                try:
                    count = locator_group.count()
                except Exception:
                    break
                for index in range(count):
                    locator = locator_group.nth(index)
                    try:
                        self._call(locator.wait_for, state="visible", timeout_ms=250)
                    except Exception:
                        continue
                    return locator
                time.sleep(0.1)
            return None

        locator = self._first_locator(locator_group)
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

    def _click_first_required(
        self, selectors_to_try: tuple[str, ...], name: str, timeout_ms: int
    ) -> None:
        for selector in selectors_to_try:
            if self._click_optional(selector, timeout_ms):
                return
        raise RuntimeError(f"{name} was not found during Dzen login")

    def _ensure_no_captcha(self) -> None:
        if self._first_visible(selectors.AUTH_CAPTCHA, 1000) is not None:
            raise RuntimeError("Dzen login requires captcha or additional verification")

    def _handle_manual_code_if_visible(self) -> bool:
        self._confirm_secure_login_if_visible()
        if self._is_yandex_phone_login_visible():
            return False

        code_input = self._first_visible(
            selectors.AUTH_CODE_INPUT,
            self._short_timeout_ms,
        )
        if code_input is None:
            return False
        if self._auth_assistant is None:
            raise RuntimeError("Dzen login requires manual code input")

        if self._restart_on_sms:
            self._auth_assistant.notify_sms_restart()
            raise DzenSmsRestartRequested()

        code = self._auth_assistant.relay_code_prompt(
            "Дзен запросил код подтверждения. Пришли код из SMS или приложения ответом в этот чат."
        ).strip()
        if not code:
            raise RuntimeError("Dzen login received an empty manual code")

        self._fill_auth_code(code_input, code)
        self._click_optional(selectors.YANDEX_ID_CONTINUE, self._short_timeout_ms)
        self._wait_after_transition()
        self._ensure_no_captcha()
        return True

    def _confirm_secure_login_if_visible(self) -> bool:
        if self._first_visible(selectors.YANDEX_ID_SECURE_LOGIN, 500) is None:
            return False
        if self._auth_assistant is None:
            raise RuntimeError("Dzen secure login requires SMS confirmation")
        self._auth_assistant.notify_sms_pending()
        self._click_required(
            selectors.YANDEX_ID_CONFIRM,
            "Yandex ID Confirm button",
            self._timeout_ms,
        )
        self._wait_after_transition()
        self._ensure_no_captcha()
        return True

    def _select_yandex_account_if_visible(self) -> bool:
        for selector in selectors.YANDEX_ID_ACCOUNT_SELECTORS:
            if self._click_optional(selector, self._short_timeout_ms):
                self._wait_after_transition()
                self._ensure_no_captcha()
                return True
        if self._is_account_choice_visible():
            raise RuntimeError(
                "Yandex ID account chooser is shown but the account card could not "
                "be clicked; update YANDEX_ID_ACCOUNT_CARD selectors for the current DOM"
            )
        return False

    def _is_account_choice_visible(self) -> bool:
        return self._first_visible(selectors.YANDEX_ID_ACCOUNT_CHOICE, 500) is not None

    def _dismiss_webauthn_promo_if_visible(self) -> bool:
        return self._click_optional(
            selectors.YANDEX_WEBAUTHN_PROMO_DISMISS, self._short_timeout_ms
        )

    def _fill_auth_code(self, first_code_input: Any, code: str) -> None:
        locator_group = self._page.locator(selectors.AUTH_CODE_INPUT)
        if hasattr(locator_group, "count") and hasattr(locator_group, "nth"):
            visible_inputs = []
            try:
                count = locator_group.count()
            except Exception:
                count = 0
            for index in range(count):
                locator = locator_group.nth(index)
                try:
                    self._call(locator.wait_for, state="visible", timeout_ms=250)
                except Exception:
                    continue
                visible_inputs.append(locator)
            if len(visible_inputs) > 1 and len(code) >= len(visible_inputs):
                for locator, char in zip(visible_inputs, code):
                    self._call(locator.fill, char, timeout_ms=self._short_timeout_ms)
                return

        self._call(first_code_input.fill, code, timeout_ms=self._short_timeout_ms)

    def _is_yandex_phone_login_visible(self) -> bool:
        return self._first_visible(selectors.YANDEX_ID_PHONE_TAB, 500) is not None

    def _wait_for_yandex_phone_login_to_advance(self) -> None:
        deadline = time.monotonic() + self._short_timeout_ms / 1000
        while time.monotonic() < deadline:
            if not self._is_yandex_phone_login_visible():
                return
            time.sleep(0.1)

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

    def _yandex_login_phone(self) -> str:
        phone = self._phone.strip()
        if phone.startswith("+"):
            return phone

        digits = re.sub(r"\D", "", phone)
        if len(digits) == 10:
            return f"+7{digits}"
        if len(digits) == 11 and digits.startswith("8"):
            return f"+7{digits[1:]}"
        return phone

    def _is_email_login(self) -> bool:
        return "@" in self._phone.strip()

    @staticmethod
    def _first_locator(locator: Any) -> Any:
        first = getattr(locator, "first", None)
        if first is None:
            return locator
        return first() if callable(first) else first

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
