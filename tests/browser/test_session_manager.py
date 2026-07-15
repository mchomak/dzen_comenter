import inspect

import dzen_commenter.browser  # noqa: F401
from dzen_commenter.browser import PlaywrightSessionManager
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.interfaces import SessionManager
from dzen_commenter.dzen import selectors


def make_settings(**overrides) -> Settings:
    base = dict(
        DATABASE_URL="postgresql://x",
        AI_PROVIDER="openai",
        AI_MODEL="test-model",
        AI_API_KEY="secret-key",
        AI_BASE_URL="https://api.example.com/v1",
        AI_TEMPERATURE=0.3,
        AI_MAX_TOKENS=64,
        AI_PROMPT_LANGUAGE="ru",
        USER_DATA_DIR="/tmp/u",
        STORAGE_STATE_PATH="/tmp/s.json",
        HEADLESS=True,
        COMMENTS_URL="https://dzen.ru/x",
        POLL_INTERVAL=30,
        KEEPALIVE_INTERVAL=300,
        AUTO_PUBLISH=False,
        MAX_REPLIES_PER_CYCLE=5,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


class FakePage:
    """Фейковая Playwright-страница: записывает вызовы, без сети/браузера."""

    def __init__(self, *, login_form_present: bool = False) -> None:
        self.login_form_present = login_form_present
        self.goto_calls: list[str] = []
        self.reload_count = 0
        self.url = "about:blank"

    def goto(self, url: str) -> None:
        self.goto_calls.append(url)
        self.url = url

    def reload(self) -> None:
        self.reload_count += 1

    def query_selector(self, selector: str):
        if selector == selectors.LOGIN_FORM and self.login_form_present:
            return object()
        return None


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]
        self.launch_kwargs = None
        self.storage_state_calls: list[str] = []
        self.add_cookies_calls: list[list[dict]] = []

    def new_page(self) -> FakePage:  # pragma: no cover - pages непустой в тестах
        page = FakePage()
        self.pages.append(page)
        return page

    def storage_state(self, *, path: str) -> None:
        self.storage_state_calls.append(path)

    def add_cookies(self, cookies: list[dict]) -> None:
        self.add_cookies_calls.append(cookies)


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self._context = context

    def launch_persistent_context(self, **kwargs) -> FakeContext:
        self._context.launch_kwargs = kwargs
        return self._context


class FakePlaywright:
    def __init__(self, context: FakeContext) -> None:
        self.chromium = FakeChromium(context)

    def start(self) -> "FakePlaywright":
        return self


def make_factory(context: FakeContext):
    def factory():
        return FakePlaywright(context)

    return factory


# Acceptance 2 — структурное соответствие контракту SessionManager.
def test_implements_session_manager_contract():
    for name in ("start", "is_logged_in", "login", "save_state", "restore"):
        proto_sig = inspect.signature(getattr(SessionManager, name))
        impl_sig = inspect.signature(getattr(PlaywrightSessionManager, name))
        assert list(proto_sig.parameters) == list(impl_sig.parameters)


# Acceptance 3 — start() поднимает persistent-контекст с конфигом из Settings.
def test_start_launches_persistent_context_with_settings():
    settings = make_settings(USER_DATA_DIR="/tmp/data", HEADLESS=True)
    page = FakePage()
    context = FakeContext(page)
    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))

    mgr.start()

    assert context.launch_kwargs["user_data_dir"] == settings.USER_DATA_DIR
    assert context.launch_kwargs["headless"] == settings.HEADLESS
    assert page.goto_calls == [settings.COMMENTS_URL]


# Acceptance 4 — save_state() пишет storage_state в STORAGE_STATE_PATH.
def test_save_state_writes_to_storage_state_path():
    settings = make_settings(STORAGE_STATE_PATH="/tmp/custom-state.json")
    page = FakePage()
    context = FakeContext(page)
    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))

    mgr.start()
    mgr.save_state()

    assert context.storage_state_calls == [settings.STORAGE_STATE_PATH]


def test_login_delegates_to_dzen_authenticator_and_saves_state(monkeypatch):
    settings = make_settings(
        COMMENTS_URL="https://dzen.ru/profile/comments",
        DZEN_LOGIN_PHONE="+79269549196",
        DZEN_LOGIN_PASSWORD="secret",
        DZEN_LOGIN_TIMEOUT_MS=12345,
    )
    auth_assistant = object()
    page = FakePage(login_form_present=False)
    context = FakeContext(page)
    captured = {}

    class FakeAuthenticator:
        def __init__(self, page_arg, **kwargs):
            captured["page"] = page_arg
            captured["kwargs"] = kwargs

        def login(self):
            captured["login_called"] = True
            return True

    monkeypatch.setattr(
        "dzen_commenter.browser.session_manager.DzenLoginAuthenticator",
        FakeAuthenticator,
    )

    mgr = PlaywrightSessionManager(
        settings,
        auth_assistant=auth_assistant,
        playwright_factory=make_factory(context),
    )
    mgr.start()
    page.goto_calls.clear()

    assert mgr.login() is True
    assert captured == {
        "page": page,
        "kwargs": {
            "comments_url": settings.COMMENTS_URL,
            "phone": settings.DZEN_LOGIN_PHONE,
            "password": settings.DZEN_LOGIN_PASSWORD,
            "auth_assistant": auth_assistant,
            "timeout_ms": settings.DZEN_LOGIN_TIMEOUT_MS,
        },
        "login_called": True,
    }
    assert page.goto_calls == [settings.COMMENTS_URL]
    assert context.storage_state_calls == [settings.STORAGE_STATE_PATH]


def test_login_returns_false_when_authenticator_skips(monkeypatch):
    settings = make_settings()
    page = FakePage(login_form_present=True)
    context = FakeContext(page)

    class FakeAuthenticator:
        def __init__(self, page_arg, **kwargs):
            pass

        def login(self):
            return False

    monkeypatch.setattr(
        "dzen_commenter.browser.session_manager.DzenLoginAuthenticator",
        FakeAuthenticator,
    )

    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))
    mgr.start()

    assert mgr.login() is False
    assert context.storage_state_calls == []


# Acceptance 4 (файл) — реальная запись в tmp-путь создаёт файл.
def test_save_state_creates_file_when_fake_writes(tmp_path):
    state_path = tmp_path / "state.json"
    settings = make_settings(STORAGE_STATE_PATH=str(state_path))

    page = FakePage()

    class WritingContext(FakeContext):
        def storage_state(self, *, path: str) -> None:
            super().storage_state(path=path)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")

    context = WritingContext(page)
    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))
    mgr.start()
    mgr.save_state()

    assert state_path.exists()


# Acceptance 5 — restore() True при существующем файле, страница без формы входа.
def test_restore_true_when_state_file_exists(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    settings = make_settings(STORAGE_STATE_PATH=str(state_path))

    page = FakePage(login_form_present=False)
    context = FakeContext(page)
    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))
    mgr.start()

    assert mgr.restore() is True
    # переход на COMMENTS_URL произошёл (start + restore).
    assert settings.COMMENTS_URL in page.goto_calls


def test_restore_loads_saved_cookies_before_opening_comments(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        '{"cookies": [{"name": "session", "value": "token", "domain": ".dzen.ru", "path": "/"}]}',
        encoding="utf-8",
    )
    settings = make_settings(STORAGE_STATE_PATH=str(state_path))
    page = FakePage(login_form_present=False)
    context = FakeContext(page)
    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))
    mgr.start()

    assert mgr.restore() is True
    assert context.add_cookies_calls == [
        [{"name": "session", "value": "token", "domain": ".dzen.ru", "path": "/"}]
    ]


# Acceptance 5 — restore() False при отсутствии файла, на страницу не ходим.
def test_restore_false_when_state_file_missing(tmp_path):
    state_path = tmp_path / "missing.json"
    settings = make_settings(STORAGE_STATE_PATH=str(state_path))

    page = FakePage()
    context = FakeContext(page)
    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))
    mgr.start()
    page.goto_calls.clear()

    assert mgr.restore() is False
    assert page.goto_calls == []


# Acceptance 6 — детект разлогина.
def test_is_logged_in_detects_login_form():
    settings = make_settings()

    page_in = FakePage(login_form_present=False)
    mgr_in = PlaywrightSessionManager(
        settings, playwright_factory=make_factory(FakeContext(page_in))
    )
    mgr_in.start()
    assert mgr_in.is_logged_in() is True

    page_out = FakePage(login_form_present=True)
    mgr_out = PlaywrightSessionManager(
        settings, playwright_factory=make_factory(FakeContext(page_out))
    )
    mgr_out.start()
    assert mgr_out.is_logged_in() is False


def test_is_logged_in_false_when_redirected_off_comments_host():
    settings = make_settings(COMMENTS_URL="https://dzen.ru/profile/comments")
    page = FakePage(login_form_present=False)
    mgr = PlaywrightSessionManager(
        settings, playwright_factory=make_factory(FakeContext(page))
    )
    mgr.start()
    page.url = "https://passport.yandex.ru/auth"

    assert mgr.is_logged_in() is False


def test_is_logged_in_false_when_redirected_off_comments_path():
    settings = make_settings(COMMENTS_URL="https://dzen.ru/profile/comments")
    page = FakePage(login_form_present=False)
    mgr = PlaywrightSessionManager(
        settings, playwright_factory=make_factory(FakeContext(page))
    )
    mgr.start()
    page.url = "https://dzen.ru/"

    assert mgr.is_logged_in() is False


def test_is_logged_in_false_before_start():
    settings = make_settings()
    mgr = PlaywrightSessionManager(settings, playwright_factory=make_factory(FakeContext(FakePage())))
    assert mgr.is_logged_in() is False


# Acceptance 2 (4A) — property page: None до start(), тот же объект после start().
def test_page_property_none_before_start_then_playwright_page():
    settings = make_settings()
    page = FakePage()
    mgr = PlaywrightSessionManager(
        settings, playwright_factory=make_factory(FakeContext(page))
    )
    assert mgr.page is None

    mgr.start()
    assert mgr.page is page


# Acceptance 7 — keep_alive() — ровно один reload, ничего не публикует.
def test_keep_alive_reloads_once():
    settings = make_settings()
    page = FakePage()
    mgr = PlaywrightSessionManager(
        settings, playwright_factory=make_factory(FakeContext(page))
    )
    mgr.start()
    mgr.keep_alive()

    assert page.reload_count == 1
