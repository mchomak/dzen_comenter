from types import SimpleNamespace

import main
from dzen_commenter.monitoring.developer_notifier import DeveloperNotifier


def make_fake_settings(**overrides):
    base = dict(
        DATABASE_URL="postgresql://x",
        AI_PROMPT_LANGUAGE="ru",
        PROMPT_CONFIG_PATH="",
        EMAIL_FALLBACK_LIST="",
        SMTP_HOST="",
        SMTP_PORT=587,
        SMTP_USER="",
        SMTP_PASSWORD="",
        SMTP_FROM="",
        TELEGRAM_BOT_TOKEN="tok",
        TELEGRAM_CHAT_ID="chat",
        DEVELOPER_TELEGRAM_CHAT_ID_LIST="developer-chat",
        TELEGRAM_PROXY_URL="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class Recorder:
    """Общий журнал событий/конструирований для spy-фейков."""

    def __init__(self):
        self.events = []


def install_di_fakes(monkeypatch):
    rec = Recorder()

    def fake_create_engine(url):
        engine = SimpleNamespace(_kind="engine", url=url)
        rec.events.append(("create_engine", url, engine))
        return engine

    class FakeRepository:
        def __init__(self, engine):
            self.engine = engine
            rec.events.append(("repository", self))

    def fake_create_provider(settings):
        provider = SimpleNamespace(_kind="provider", settings=settings)
        rec.events.append(("create_provider", settings, provider))
        return provider

    class FakePromptBuilder:
        def __init__(self, language=None, config_path=None):
            self.language = language
            self.config_path = config_path
            rec.events.append(("prompt_builder", self))

    class FakeSession:
        def __init__(self, settings, **kwargs):
            self.settings = settings
            self.kwargs = kwargs
            self.page = SimpleNamespace(_kind="playwright_page")
            self.start_calls = 0
            rec.events.append(("session_init", self))

        def start(self):
            self.start_calls += 1
            rec.events.append(("session_start", self))

    class FakeDzenPage:
        def __init__(self, page):
            self.page = page
            rec.events.append(("dzen_page", self, page))

    class FakeEmailFallback:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            rec.events.append(("email_fallback", self))

    class FakeTelegramNotifier:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            rec.events.append(("telegram_notifier", self))

    class FakeAuthAssistant:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            rec.events.append(("auth_assistant", self))

    class FakeOrchestratorLoop:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            rec.events.append(("loop", self))

    monkeypatch.setattr(main.sqlalchemy, "create_engine", fake_create_engine)
    monkeypatch.setattr(main, "PostgresCommentRepository", FakeRepository)
    monkeypatch.setattr(main, "create_provider", fake_create_provider)
    monkeypatch.setattr(main, "DameoPromptBuilder", FakePromptBuilder)
    monkeypatch.setattr(main, "PlaywrightSessionManager", FakeSession)
    monkeypatch.setattr(main, "DzenStudioPage", FakeDzenPage)
    monkeypatch.setattr(main, "EmailFallbackNotifier", FakeEmailFallback)
    monkeypatch.setattr(main, "TelegramNotifier", FakeTelegramNotifier)
    monkeypatch.setattr(main, "TelegramAuthAssistant", FakeAuthAssistant)
    monkeypatch.setattr(main, "OrchestratorLoop", FakeOrchestratorLoop)
    return rec


def _first(rec, name):
    for ev in rec.events:
        if ev[0] == name:
            return ev
    raise AssertionError(f"event {name!r} not recorded")


# Acceptance 1 — импортируемость публичного API.
def test_public_api_importable():
    from main import build_app, main as main_entry, run_supervised

    assert callable(build_app)
    assert callable(run_supervised)
    assert callable(main_entry)


# Acceptance 3 — DI-сборка build_app.
def test_build_app_wires_layers(monkeypatch):
    rec = install_di_fakes(monkeypatch)
    settings = make_fake_settings()

    loop, session, notifier = main.build_app(settings)

    # session.start() ровно 1 раз, ДО конструирования DzenStudioPage.
    assert session.start_calls == 1
    names = [ev[0] for ev in rec.events]
    assert names.index("session_start") < names.index("dzen_page")

    # DzenStudioPage сконструирован с session.page (идентичность).
    dzen_ev = _first(rec, "dzen_page")
    assert dzen_ev[2] is session.page

    # create_provider вызван с тем же объектом settings.
    provider_ev = _first(rec, "create_provider")
    assert provider_ev[1] is settings

    # DameoPromptBuilder: language и config_path из settings (пустой → None).
    pb = _first(rec, "prompt_builder")[1]
    assert pb.language == settings.AI_PROMPT_LANGUAGE
    assert pb.config_path is None

    # EMAIL_FALLBACK_LIST="" → fallback=None (EmailFallbackNotifier не создан).
    assert all(ev[0] != "email_fallback" for ev in rec.events)
    tg = _first(rec, "telegram_notifier")[1]
    assert tg.kwargs["fallback"] is None
    auth_assistant = _first(rec, "auth_assistant")[1]
    assert session.kwargs["auth_assistant"] is auth_assistant

    # OrchestratorLoop — ровно 9 keyword-аргументов, каждый идентичен фейку.
    loop_kwargs = loop.kwargs
    assert set(loop_kwargs) == {
        "settings",
        "repository",
        "ai_provider",
        "prompt_builder",
        "session",
        "page",
        "notifier",
        "auth_assistant",
        "classify_reply_type",
    }
    assert loop_kwargs["settings"] is settings
    assert loop_kwargs["repository"] is _first(rec, "repository")[1]
    assert loop_kwargs["ai_provider"] is provider_ev[2]
    assert loop_kwargs["prompt_builder"] is pb
    assert loop_kwargs["session"] is session
    assert loop_kwargs["page"] is dzen_ev[1]
    assert isinstance(loop_kwargs["notifier"], DeveloperNotifier)
    assert loop_kwargs["notifier"].transport is tg
    assert tg.kwargs["chat_id"] == "developer-chat"
    assert auth_assistant.kwargs["chat_id"] == "chat"
    assert loop_kwargs["auth_assistant"] is auth_assistant
    assert loop_kwargs["classify_reply_type"] is main.classify_reply_type

    # Возврат — те же объекты.
    assert loop is _first(rec, "loop")[1]
    assert session is _first(rec, "session_init")[1]
    assert notifier.transport is tg


# Acceptance 3 — email-фоллбэк собирается при непустом списке и SMTP_HOST.
def test_build_app_email_fallback_configured(monkeypatch):
    rec = install_di_fakes(monkeypatch)
    settings = make_fake_settings(
        EMAIL_FALLBACK_LIST="a@x.com, b@y.com",
        SMTP_HOST="smtp.example.com",
    )

    main.build_app(settings)

    ef = _first(rec, "email_fallback")[1]
    assert ef.kwargs["to_addrs"] == ["a@x.com", "b@y.com"]

    tg = _first(rec, "telegram_notifier")[1]
    assert tg.kwargs["fallback"] is ef


class FakeLoop:
    def __init__(self, raise_on=()):
        self.raise_on = set(raise_on)
        self.cycle_calls = 0

    def run_cycle(self):
        self.cycle_calls += 1
        if self.cycle_calls in self.raise_on:
            raise RuntimeError(f"boom on cycle {self.cycle_calls}")


class FakeSession:
    def __init__(self):
        self.keep_alive_calls = 0

    def keep_alive(self):
        self.keep_alive_calls += 1


class FakeNotifier:
    def __init__(self):
        self.errors = []

    def notify_error(self, message, error=None):
        self.errors.append((message, error))


# Acceptance 4 — happy path: run_cycle и sleep_fn вызваны ровно max_cycles раз.
def test_run_supervised_happy_path():
    loop = FakeLoop()
    session = FakeSession()
    notifier = FakeNotifier()
    sleeps = []

    main.run_supervised(
        loop,
        session,
        notifier,
        poll_interval=30,
        keepalive_interval=300,
        sleep_fn=lambda s: sleeps.append(s),
        time_fn=lambda: 0.0,
        max_cycles=3,
    )

    assert loop.cycle_calls == 3
    assert sleeps == [30, 30, 30]
    assert notifier.errors == []
    assert session.keep_alive_calls == 0


# Acceptance 5 — устойчивость к исключению run_cycle.
def test_run_supervised_survives_exception():
    loop = FakeLoop(raise_on=(1,))
    session = FakeSession()
    notifier = FakeNotifier()

    main.run_supervised(
        loop,
        session,
        notifier,
        poll_interval=1,
        keepalive_interval=1000,
        sleep_fn=lambda s: None,
        time_fn=lambda: 0.0,
        max_cycles=2,
    )

    assert loop.cycle_calls == 2
    assert len(notifier.errors) == 1


# Acceptance 6 — keep-alive срабатывает по таймеру, не на каждой итерации.
def test_run_supervised_keepalive_by_timer():
    loop = FakeLoop()
    session = FakeSession()
    notifier = FakeNotifier()
    times = iter([0.0, 5.0, 10.0, 15.0, 20.0])

    main.run_supervised(
        loop,
        session,
        notifier,
        poll_interval=1,
        keepalive_interval=10,
        sleep_fn=lambda s: None,
        time_fn=lambda: next(times),
        max_cycles=4,
    )

    assert session.keep_alive_calls == 2
