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

    def click(self, timeout=None):
        self.page.clicks.append(self.selector)
        if self.selector == selectors.LOGIN_BUTTON:
            self.page.visible[selectors.LOGIN_PHONE_INPUT] = True
        elif self.selector == selectors.LOGIN_PHONE_CONTINUE:
            self.page.visible[selectors.VK_PASSWORD_METHOD] = True
        elif self.selector == selectors.VK_PASSWORD_METHOD:
            self.page.visible[selectors.VK_PASSWORD_INPUT] = True


class FakePage:
    def __init__(self, *, captcha_visible=False):
        self.visible = {
            selectors.LOGIN_BUTTON: True,
            selectors.LOGIN_PHONE_INPUT: False,
            selectors.LOGIN_PHONE_CONTINUE: True,
            selectors.VK_PASSWORD_METHOD: False,
            selectors.VK_PASSWORD_INPUT: False,
            selectors.VK_PASSWORD_SUBMIT: True,
            selectors.VK_ALLOW_ACCESS: False,
            selectors.AUTH_CAPTCHA: captcha_visible,
        }
        self.goto_calls = []
        self.clicks = []
        self.fills = []
        self.load_states = []
        self.waited_urls = []

    def goto(self, url):
        self.goto_calls.append(url)

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_load_state(self, state, timeout=None):
        self.load_states.append(state)

    def wait_for_url(self, url, timeout=None):
        self.waited_urls.append(url)


def test_dzen_login_fills_phone_then_password():
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
        (selectors.VK_PASSWORD_INPUT, "secret"),
    ]
    assert page.clicks == [
        selectors.LOGIN_BUTTON,
        selectors.LOGIN_PHONE_CONTINUE,
        selectors.VK_PASSWORD_METHOD,
        selectors.VK_PASSWORD_SUBMIT,
    ]
    assert page.waited_urls


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
