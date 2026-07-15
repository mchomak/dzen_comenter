import pytest

from dzen_commenter.auth import DzenLoginAuthenticator
from dzen_commenter.dzen import selectors


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def first(self):
        return self

    def wait_for(self, *, state, timeout=None):
        if state != "visible" or not self.page.visible.get(self.selector, False):
            raise TimeoutError(self.selector)

    def fill(self, value, timeout=None):
        self.page.fills.append((self.selector, value))
        if self.selector == selectors.AUTH_CODE_INPUT:
            self.page.code_entered = value

    def click(self, timeout=None):
        self.page.clicks.append(self.selector)
        if self.selector == selectors.LOGIN_BUTTON:
            self.page.visible[selectors.LOGIN_PHONE_INPUT] = True
            self.page.visible[selectors.YANDEX_ID_LOGIN] = self.page.yandex_visible
        elif self.selector == selectors.LOGIN_PHONE_CONTINUE:
            self.page.visible[selectors.VK_PASSWORD_METHOD] = True
        elif self.selector == selectors.YANDEX_ID_LOGIN:
            if self.page.yandex_more_visible:
                self.page.visible[selectors.YANDEX_ID_MORE_BUTTON] = True
            else:
                self.page.visible[selectors.YANDEX_ID_LOGIN_INPUT] = True
                self.page.visible[selectors.YANDEX_ID_PHONE_TAB] = True
        elif self.selector == selectors.YANDEX_ID_MORE_BUTTON:
            self.page.visible[selectors.YANDEX_ID_MORE_BUTTON] = False
            self.page.visible[selectors.YANDEX_ID_USERNAME_LOGIN] = True
        elif self.selector == selectors.YANDEX_ID_USERNAME_LOGIN:
            self.page.visible[selectors.YANDEX_ID_USERNAME_LOGIN] = False
            self.page.visible[selectors.YANDEX_ID_USERNAME_INPUT] = True
        elif self.selector == selectors.YANDEX_ID_CONTINUE:
            if self.page.visible[selectors.AUTH_CODE_INPUT] and self.page.code_entered:
                self.page.visible[selectors.AUTH_CODE_INPUT] = False
                self.page.visible[selectors.YANDEX_ID_LOGIN_INPUT] = False
                self.page.visible[selectors.YANDEX_ID_PHONE_TAB] = False
                if self.page.account_choice_visible:
                    self.page.visible[selectors.YANDEX_ID_ACCOUNT_CHOICE] = True
                    if self.page.account_card_unclickable:
                        # Экран «Выберите аккаунт» показан, но карточку кликнуть нельзя,
                        # а телефонное поле остаётся в DOM (реальный баг pwl-страницы).
                        self.page.visible[selectors.YANDEX_ID_LOGIN_INPUT] = True
                    else:
                        self.page.visible[selectors.YANDEX_ID_ACCOUNT_CARD] = True
                else:
                    self.page.visible[selectors.VK_PASSWORD_INPUT] = True
            elif self.page.code_visible:
                if self.page.phone_form_retries_before_code > 0:
                    self.page.phone_form_retries_before_code -= 1
                    self.page.visible[selectors.AUTH_CODE_INPUT] = True
                    self.page.visible[selectors.YANDEX_ID_LOGIN_INPUT] = True
                    self.page.visible[selectors.YANDEX_ID_PHONE_TAB] = True
                    return
                self.page.visible[selectors.AUTH_CODE_INPUT] = True
                self.page.visible[selectors.YANDEX_ID_LOGIN_INPUT] = False
                self.page.visible[selectors.YANDEX_ID_PHONE_TAB] = False
            else:
                self.page.visible[selectors.VK_PASSWORD_INPUT] = True
                self.page.visible[selectors.YANDEX_ID_LOGIN_INPUT] = False
                self.page.visible[selectors.YANDEX_ID_PHONE_TAB] = False
        elif self.selector == selectors.YANDEX_ID_ACCOUNT_CARD:
            self.page.visible[selectors.YANDEX_ID_ACCOUNT_CARD] = False
            if self.page.webauthn_promo_visible:
                self.page.visible[selectors.YANDEX_WEBAUTHN_PROMO_DISMISS] = True
            else:
                self.page.url = "https://dzen.ru/profile/comments"
        elif self.selector == selectors.YANDEX_WEBAUTHN_PROMO_DISMISS:
            self.page.visible[selectors.YANDEX_WEBAUTHN_PROMO_DISMISS] = False
            self.page.url = "https://dzen.ru/profile/comments"
        elif self.selector == selectors.VK_PASSWORD_METHOD:
            self.page.visible[selectors.VK_PASSWORD_INPUT] = True


class FakePage:
    def __init__(
        self,
        *,
        captcha_visible=False,
        yandex_visible=True,
        code_visible=False,
        account_choice_visible=False,
        account_card_unclickable=False,
        webauthn_promo_visible=False,
        phone_form_retries_before_code=0,
        yandex_more_visible=False,
    ):
        self.yandex_visible = yandex_visible
        self.code_visible = code_visible
        self.account_choice_visible = account_choice_visible
        self.account_card_unclickable = account_card_unclickable
        self.webauthn_promo_visible = webauthn_promo_visible
        self.phone_form_retries_before_code = phone_form_retries_before_code
        self.yandex_more_visible = yandex_more_visible
        self.visible = {
            selectors.LOGIN_BUTTON: True,
            selectors.LOGIN_PHONE_INPUT: False,
            selectors.LOGIN_PHONE_CONTINUE: True,
            selectors.YANDEX_ID_LOGIN: False,
            selectors.YANDEX_ID_LOGIN_INPUT: False,
            selectors.YANDEX_ID_USERNAME_INPUT: False,
            selectors.YANDEX_ID_PHONE_TAB: False,
            selectors.YANDEX_ID_MORE_BUTTON: False,
            selectors.YANDEX_ID_USERNAME_LOGIN: False,
            selectors.YANDEX_ID_CONTINUE: True,
            selectors.VK_PASSWORD_METHOD: False,
            selectors.VK_PASSWORD_INPUT: False,
            selectors.VK_PASSWORD_SUBMIT: True,
            selectors.VK_ALLOW_ACCESS: False,
            selectors.AUTH_CAPTCHA: captcha_visible,
            selectors.AUTH_CODE_INPUT: False,
            selectors.YANDEX_ID_ACCOUNT_CARD: False,
            selectors.YANDEX_WEBAUTHN_PROMO_DISMISS: False,
        }
        self.url = ""
        self.goto_calls = []
        self.clicks = []
        self.fills = []
        self.code_entered = ""
        self.load_states = []
        self.waited_urls = []

    def goto(self, url):
        self.goto_calls.append(url)
        self.url = url

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_load_state(self, state, timeout=None):
        self.load_states.append(state)

    def wait_for_url(self, url, timeout=None):
        self.waited_urls.append(url)


def test_dzen_login_prefers_yandex_id_after_phone():
    page = FakePage()
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="+79269549196",
        password="secret",
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert page.goto_calls == ["https://dzen.ru/profile/comments"]
    assert page.fills == [
        (selectors.LOGIN_PHONE_INPUT, "+79269549196"),
        (selectors.YANDEX_ID_LOGIN_INPUT, "+79269549196"),
        (selectors.VK_PASSWORD_INPUT, "secret"),
    ]
    assert page.clicks == [
        selectors.LOGIN_BUTTON,
        selectors.YANDEX_ID_LOGIN,
        selectors.YANDEX_ID_PHONE_TAB,
        selectors.YANDEX_ID_CONTINUE,
        selectors.VK_PASSWORD_SUBMIT,
    ]
    assert page.waited_urls


def test_dzen_login_falls_back_to_vk_phone_continue_without_yandex_id():
    page = FakePage(yandex_visible=False)
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="+79269549196",
        password="secret",
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert page.clicks == [
        selectors.LOGIN_BUTTON,
        selectors.LOGIN_PHONE_CONTINUE,
        selectors.VK_PASSWORD_METHOD,
        selectors.VK_PASSWORD_SUBMIT,
    ]


def test_dzen_login_adds_country_code_for_yandex_phone_input():
    page = FakePage()
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="9269549196",
        password="secret",
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert page.fills == [
        (selectors.LOGIN_PHONE_INPUT, "9269549196"),
        (selectors.YANDEX_ID_LOGIN_INPUT, "+79269549196"),
        (selectors.VK_PASSWORD_INPUT, "secret"),
    ]


def test_dzen_login_uses_more_then_username_for_email():
    page = FakePage(yandex_more_visible=True)
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="user@example.com",
        password="secret",
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert page.fills == [
        (selectors.YANDEX_ID_USERNAME_INPUT, "user@example.com"),
        (selectors.VK_PASSWORD_INPUT, "secret"),
    ]
    assert page.clicks == [
        selectors.LOGIN_BUTTON,
        selectors.YANDEX_ID_LOGIN,
        selectors.YANDEX_ID_MORE_BUTTON,
        selectors.YANDEX_ID_USERNAME_LOGIN,
        selectors.YANDEX_ID_CONTINUE,
        selectors.VK_PASSWORD_SUBMIT,
    ]


def test_dzen_login_stops_when_manual_code_is_visible():
    page = FakePage(code_visible=True)
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="9269549196",
        password="secret",
        timeout_ms=1000,
    )

    with pytest.raises(RuntimeError, match="manual code"):
        authenticator.login()


def test_dzen_login_relays_manual_code_through_auth_assistant():
    page = FakePage(code_visible=True)

    class FakeAuthAssistant:
        def __init__(self):
            self.prompts = []

        def relay_code_prompt(self, prompt_text):
            self.prompts.append(prompt_text)
            return "482913"

    auth_assistant = FakeAuthAssistant()
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="9269549196",
        password="secret",
        auth_assistant=auth_assistant,
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert auth_assistant.prompts == [
        "Дзен запросил код подтверждения. Пришли код из SMS или приложения ответом в этот чат."
    ]
    assert (selectors.AUTH_CODE_INPUT, "482913") in page.fills
    assert page.fills[-1] == (selectors.VK_PASSWORD_INPUT, "secret")
    assert page.clicks.count(selectors.YANDEX_ID_CONTINUE) == 2


def test_dzen_login_does_not_request_code_on_phone_login_form():
    page = FakePage(code_visible=True, phone_form_retries_before_code=1)

    class FakeAuthAssistant:
        def __init__(self):
            self.prompts = []

        def relay_code_prompt(self, prompt_text):
            self.prompts.append(prompt_text)
            return "482913"

    auth_assistant = FakeAuthAssistant()
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="9269549196",
        password="secret",
        auth_assistant=auth_assistant,
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert len(auth_assistant.prompts) == 1
    assert page.clicks.count(selectors.YANDEX_ID_CONTINUE) == 3
    assert (selectors.AUTH_CODE_INPUT, "482913") in page.fills


def test_dzen_login_selects_account_after_manual_code():
    page = FakePage(code_visible=True, account_choice_visible=True)

    class FakeAuthAssistant:
        def relay_code_prompt(self, prompt_text):
            return "482913"

    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="9269549196",
        password="secret",
        auth_assistant=FakeAuthAssistant(),
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert (selectors.AUTH_CODE_INPUT, "482913") in page.fills
    assert selectors.YANDEX_ID_ACCOUNT_CARD in page.clicks
    assert (selectors.VK_PASSWORD_INPUT, "secret") not in page.fills


def test_dzen_login_dismisses_webauthn_promo_after_account_selection():
    # После выбора аккаунта Яндекс может показать промо «Войти по лицу/отпечатку».
    # Бот обязан нажать «Напомнить позже», а не «Let's do it!»/«Войти», и на этом
    # завершить авторизацию без ввода пароля.
    page = FakePage(
        code_visible=True,
        account_choice_visible=True,
        webauthn_promo_visible=True,
    )

    class FakeAuthAssistant:
        def relay_code_prompt(self, prompt_text):
            return "482913"

    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="9269549196",
        password="secret",
        auth_assistant=FakeAuthAssistant(),
        timeout_ms=1000,
    )

    assert authenticator.login() is True

    assert selectors.YANDEX_ID_ACCOUNT_CARD in page.clicks
    assert selectors.YANDEX_WEBAUTHN_PROMO_DISMISS in page.clicks
    assert (selectors.VK_PASSWORD_INPUT, "secret") not in page.fills


def test_webauthn_promo_selector_targets_dismiss_not_enable():
    assert "Напомнить позже" in selectors.YANDEX_WEBAUTHN_PROMO_DISMISS
    assert "Remind me later" in selectors.YANDEX_WEBAUTHN_PROMO_DISMISS
    assert "Let's do it" not in selectors.YANDEX_WEBAUTHN_PROMO_DISMISS
    assert "Войти" not in selectors.YANDEX_WEBAUTHN_PROMO_DISMISS


def test_dzen_login_does_not_reenter_phone_when_account_card_unclickable():
    # Регрессия: на экране «Выберите аккаунт» карточку не удалось кликнуть.
    # Бот НЕ должен снова заполнять телефон (что уводило его назад на ввод номера);
    # он обязан остановиться с понятной ошибкой.
    page = FakePage(
        code_visible=True,
        account_choice_visible=True,
        account_card_unclickable=True,
    )

    class FakeAuthAssistant:
        def relay_code_prompt(self, prompt_text):
            return "482913"

    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="9269549196",
        password="secret",
        auth_assistant=FakeAuthAssistant(),
        timeout_ms=1000,
    )

    with pytest.raises(RuntimeError, match="account chooser"):
        authenticator.login()

    # Телефон в поле Яндекс ID был введён ровно один раз (до кода) и НЕ повторно.
    phone_fills = [f for f in page.fills if f[0] == selectors.YANDEX_ID_LOGIN_INPUT]
    assert len(phone_fills) == 1
    assert (selectors.VK_PASSWORD_INPUT, "secret") not in page.fills


def test_account_choice_detector_is_scoped_to_headings_not_scripts():
    # На экране ввода телефона строка «Выберите аккаунт» присутствует только в <script>,
    # поэтому детектор обязан быть привязан к заголовку/кнопке, а не к «//*».
    assert selectors.YANDEX_ID_ACCOUNT_CHOICE.startswith("xpath=//*[self::h1")
    assert "Выберите аккаунт" in selectors.YANDEX_ID_ACCOUNT_CHOICE
    assert "//*[contains" not in selectors.YANDEX_ID_ACCOUNT_CHOICE


def test_account_card_selectors_match_real_yandex_dom():
    # Мёртвые селекторы старой разметки убраны, добавлены реальные признаки страницы.
    assert 'data-t="account-card"' not in selectors.YANDEX_ID_ACCOUNT_CARD
    assert "Плюс" in selectors.YANDEX_ID_ACCOUNT_CARD
    # Карточка ищется среди элементов ПОСЛЕ заголовка и не должна содержать <input>.
    assert "not(.//input)" in selectors.YANDEX_ID_ACCOUNT_CARD_XPATH
    assert "Нет нужного" in selectors.YANDEX_ID_ACCOUNT_CARD_XPATH


def test_auth_code_selector_does_not_match_generic_numeric_phone_input():
    assert 'input[inputmode="numeric"]' not in selectors.AUTH_CODE_INPUT
    assert 'input[type="tel"][maxlength="6"]' not in selectors.AUTH_CODE_INPUT
    assert 'input[type="text"][maxlength="6"]' not in selectors.AUTH_CODE_INPUT


@pytest.mark.parametrize(
    ("phone", "password"),
    [
        ("", "secret"),
        ("+79269549196", ""),
    ],
)
def test_dzen_login_skips_without_credentials(phone, password):
    page = FakePage()
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone=phone,
        password=password,
    )

    assert authenticator.login() is False
    assert page.goto_calls == []
    assert page.clicks == []
    assert page.fills == []


def test_dzen_login_raises_when_captcha_is_visible():
    page = FakePage(captcha_visible=True)
    authenticator = DzenLoginAuthenticator(
        page,
        comments_url="https://dzen.ru/profile/comments",
        phone="+79269549196",
        password="secret",
    )

    with pytest.raises(RuntimeError, match="captcha"):
        authenticator.login()
