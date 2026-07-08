import os

from playwright.sync_api import sync_playwright

from dzen_commenter.auth import DzenLoginAuthenticator
from dzen_commenter.config.settings import Settings
from dzen_commenter.dzen import selectors


class PlaywrightSessionManager:
    """Управляет persistent-контекстом Playwright и сессией Дзена.

    `playwright_factory` инъектируется ради тестов: по умолчанию реальный
    `sync_playwright`, в тестах — фейк, записывающий вызовы. Реальный браузер
    в тестах не поднимается.
    """

    def __init__(self, settings: Settings, *, playwright_factory=sync_playwright) -> None:
        self._settings = settings
        self._playwright_factory = playwright_factory
        self._playwright = None
        self._context = None
        self._page = None

    @property
    def page(self):
        return self._page

    def start(self) -> None:
        self._playwright = self._playwright_factory().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._settings.USER_DATA_DIR,
            headless=self._settings.HEADLESS,
        )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._page.goto(self._settings.COMMENTS_URL)

    def is_logged_in(self) -> bool:
        if self._page is None:
            return False
        return self._page.query_selector(selectors.LOGIN_FORM) is None

    def login(self) -> bool:
        if self._page is None:
            return False

        authenticator = DzenLoginAuthenticator(
            self._page,
            comments_url=self._settings.COMMENTS_URL,
            phone=self._settings.DZEN_LOGIN_PHONE,
            password=self._settings.DZEN_LOGIN_PASSWORD,
            timeout_ms=self._settings.DZEN_LOGIN_TIMEOUT_MS,
        )
        attempted = authenticator.login()
        if not attempted:
            return False

        self._page.goto(self._settings.COMMENTS_URL)
        if not self.is_logged_in():
            return False

        self.save_state()
        return True

    def save_state(self) -> None:
        self._context.storage_state(path=self._settings.STORAGE_STATE_PATH)

    def restore(self) -> bool:
        if not os.path.exists(self._settings.STORAGE_STATE_PATH):
            return False
        self._page.goto(self._settings.COMMENTS_URL)
        return self.is_logged_in()

    def keep_alive(self) -> None:
        """Лёгкий reload, чтобы сессия не протухала. НЕ в Protocol."""
        self._page.reload()
