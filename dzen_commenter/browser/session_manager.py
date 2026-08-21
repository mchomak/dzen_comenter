import json
import logging
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from dzen_commenter.auth import DzenLoginAuthenticator, DzenSmsRestartRequested
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.interfaces import AuthAssistant
from dzen_commenter.dzen import selectors


logger = logging.getLogger(__name__)


class PlaywrightSessionManager:
    """Управляет persistent-контекстом Playwright и сессией Дзена.

    `playwright_factory` инъектируется ради тестов: по умолчанию реальный
    `sync_playwright`, в тестах — фейк, записывающий вызовы. Реальный браузер
    в тестах не поднимается.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        auth_assistant: AuthAssistant | None = None,
        playwright_factory=sync_playwright,
    ) -> None:
        self._settings = settings
        self._auth_assistant = auth_assistant
        self._playwright_factory = playwright_factory
        self._playwright = None
        self._context = None
        self._page = None
        self._lock = threading.RLock()

    @property
    def page(self):
        return self._page

    @contextmanager
    def browser_access(self) -> Iterator[None]:
        """Serialize browser automation with account replacement."""
        with self._lock:
            yield

    def start(self) -> None:
        with self._lock:
            self._start()

    def _start(self) -> None:
        Path(self._settings.USER_DATA_DIR).mkdir(parents=True, exist_ok=True)
        self._playwright = self._playwright_factory().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._settings.USER_DATA_DIR,
            headless=self._settings.HEADLESS,
        )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._page.goto(self._settings.COMMENTS_URL)

    def is_logged_in(self) -> bool:
        with self._lock:
            if self._page is None:
                return False
            if not self._is_on_comments_url():
                return False
            return self._page.query_selector(selectors.LOGIN_FORM) is None

    def login(self) -> bool:
        with self._lock:
            return self._login(
                self._settings.DZEN_LOGIN_PHONE,
                self._settings.DZEN_LOGIN_PASSWORD,
            )

    def _login(self, login: str, password: str) -> bool:
        if self._page is None:
            return False

        attempted = False
        for restart_on_sms in (True, False):
            authenticator = DzenLoginAuthenticator(
                self._page,
                comments_url=self._settings.COMMENTS_URL,
                phone=login,
                password=password,
                auth_assistant=self._auth_assistant,
                timeout_ms=self._settings.DZEN_LOGIN_TIMEOUT_MS,
                restart_on_sms=restart_on_sms,
            )
            try:
                attempted = authenticator.login()
            except DzenSmsRestartRequested:
                continue
            break
        if not attempted:
            return False

        self._page.goto(self._settings.COMMENTS_URL)
        if not self.is_logged_in():
            return False

        self.save_state()
        return True

    def save_state(self) -> None:
        with self._lock:
            self._save_state()

    def _save_state(self) -> None:
        if self._context is None:
            raise RuntimeError("Playwright context is not started")
        Path(self._settings.STORAGE_STATE_PATH).parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._context.storage_state(path=self._settings.STORAGE_STATE_PATH)

    def restore(self) -> bool:
        with self._lock:
            state_path = Path(self._settings.STORAGE_STATE_PATH)
            if not state_path.exists() or self._context is None:
                return False
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                cookies = state.get("cookies", [])
                if cookies:
                    self._context.add_cookies(cookies)
            except (AttributeError, OSError, ValueError, TypeError):
                return False
            self._page.goto(self._settings.COMMENTS_URL)
            return self.is_logged_in()

    def reset_authentication(self) -> None:
        with self._lock:
            if self._context is not None:
                self._context.clear_cookies()
            Path(self._settings.STORAGE_STATE_PATH).unlink(missing_ok=True)
            if self._page is not None:
                self._page.goto(self._settings.COMMENTS_URL)

    def change_account(self, login: str, password: str) -> bool:
        """Discard this manager's session and authenticate a new Dzen account."""
        with self._lock:
            if not login.strip() or not password:
                return False
            self._close_browser_session()
            Path(self._settings.STORAGE_STATE_PATH).unlink(missing_ok=True)
            self._remove_user_profile()
            self._start()
            return self._login(login, password)

    def keep_alive(self) -> None:
        """Лёгкий reload, чтобы сессия не протухала. НЕ в Protocol."""
        with self._lock:
            try:
                self._page.reload(wait_until="domcontentloaded")
            except PlaywrightError as exc:
                if not self._is_browser_crash_error(exc):
                    raise
                logger.warning(
                    "Playwright browser session became unavailable; restarting it",
                    extra={
                        "event": "playwright_session_restart_started",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                try:
                    self._restart_browser_session()
                    logger.info(
                        "Playwright browser session restarted",
                        extra={"event": "playwright_session_restarted"},
                    )
                except Exception:
                    raise exc

    def _restart_browser_session(self) -> None:
        self._close_browser_session()
        self._start()

    def _close_browser_session(self) -> None:
        old_context, old_playwright = self._context, self._playwright
        self._page = None
        self._context = None
        self._playwright = None

        if old_context is not None:
            try:
                old_context.close()
            except Exception:
                pass
        if old_playwright is not None:
            try:
                old_playwright.stop()
            except Exception:
                pass

    def _remove_user_profile(self) -> None:
        profile_path = Path(self._settings.USER_DATA_DIR).resolve()
        if profile_path in {Path(profile_path.anchor), Path.cwd().resolve()}:
            raise RuntimeError("USER_DATA_DIR must identify a browser profile directory")
        shutil.rmtree(profile_path, ignore_errors=True)

    @staticmethod
    def _is_browser_crash_error(exc: PlaywrightError) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "page crashed",
                "browser has been closed",
                "event loop is closed",
                "target page, context or browser has been closed",
            )
        )

    def _is_on_comments_url(self) -> bool:
        current_url = getattr(self._page, "url", "")
        if not isinstance(current_url, str):
            return False

        current = urlparse(current_url)
        expected = urlparse(self._settings.COMMENTS_URL)
        if not current.netloc or current.netloc.lower() != expected.netloc.lower():
            return False

        expected_path = expected.path.rstrip("/")
        if not expected_path:
            return True
        return current.path.rstrip("/") == expected_path
