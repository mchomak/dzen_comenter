import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dzen_commenter.admin.app import create_app
from dzen_commenter.admin.config import AdminSettings
from dzen_commenter.admin.validation import validate_settings_form
from dzen_commenter.config.runtime_config import RuntimeConfig, RuntimeConfigData, RuntimeSettings
from dzen_commenter.prompt.config_loader import PromptBrandConfig


PASSWORD = "correct-horse-battery"


class FakeVncAccess:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.set_calls: list[bool] = []

    def status(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool) -> bool:
        self.set_calls.append(enabled)
        self.enabled = enabled
        return enabled


def _runtime_data() -> RuntimeConfigData:
    return RuntimeConfigData(
        settings=RuntimeSettings(
            auto_publish=True,
            max_comment_age_days=14,
            max_reply_length=450,
            cta_every_n_comments=7,
            max_comments_per_hour=100,
            developer_telegram_chat_ids="123,456",
            error_email_list="ops@example.com,dev@example.com",
        ),
        prompt=PromptBrandConfig(
            role="community manager",
            tone_of_voice="friendly",
            anti_rules="never be rude",
            task_lead="answer leads",
            task_engage="answer discussions",
            cta_marker="request an estimate",
            cta_link="https://saved.example/remont",
            language="ru",
        ),
    )


@pytest.fixture
def settings(tmp_path):
    runtime_path = tmp_path / "runtime_config.json"
    RuntimeConfig(str(runtime_path)).save(_runtime_data())
    return AdminSettings(
        _env_file=None,
        ADMIN_PASSWORD=PASSWORD,
        ADMIN_SESSION_SECRET="test-session-secret",
        RUNTIME_CONFIG_PATH=str(runtime_path),
        VNC_HOST="vnc.example.test",
        VNC_PORT=5901,
        VNC_PASSWORD="vnc-only-secret",
        DATABASE_URL="postgresql://db-secret",
    )


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings)
    test_client = TestClient(app, follow_redirects=False)
    test_client.post("/login", data={"password": PASSWORD})
    return test_client


def _form() -> dict[str, object]:
    return {
        "auto_publish": "on",
        "max_comment_age_days": "21",
        "max_reply_length": "600",
        "cta_every_n_comments": "7",
        "max_comments_per_hour": "100",
        "developer_telegram_chat_ids": ["111", "222"],
        "error_email_list": ["one@example.com", "two@example.com"],
        "error_notification_cooldown": "15m",
        "telegram_proxy_url": "",
        "batch_replies_enabled": "on",
        "batch_cutover_at": "2026-08-30T12:00:00+03:00",
        "batch_max_comments": "3",
        "batch_wait_hours": "12",
        "batch_retry_cooldown_minutes": "60",
        "batch_max_attempts_per_comment": "2",
        "role": "new role",
        "tone_of_voice": "new tone",
        "anti_rules": "new rules",
        "task_lead": "new lead task",
        "task_engage": "new engage task",
        "cta_marker": "new cta",
        "cta_link": "https://new.example/remont",
        "language": "ru",
    }


@pytest.mark.parametrize(
    ("interval", "expected_seconds"),
    (("15m", 900), (" 15m ", 900), ("2h", 7200)),
)
def test_validate_settings_form_converts_notification_interval(interval, expected_seconds):
    form = _form()
    form["error_notification_cooldown"] = interval

    data, errors = validate_settings_form(form)

    assert errors == {}
    assert data.settings.error_notification_cooldown_seconds == expected_seconds


@pytest.mark.parametrize("interval", ("", "0m", "1.5h", "15d", "25h"))
def test_validate_settings_form_rejects_invalid_notification_intervals(interval):
    form = _form()
    form["error_notification_cooldown"] = interval

    _, errors = validate_settings_form(form)

    assert "error_notification_cooldown" in errors


@pytest.mark.parametrize(
    "proxy_url",
    ("", "http://proxy.example:8080", "https://proxy.example", "socks5://proxy.example", "socks5h://proxy.example"),
)
def test_validate_settings_form_accepts_supported_telegram_proxy_urls(proxy_url):
    form = _form()
    form["telegram_proxy_url"] = proxy_url

    data, errors = validate_settings_form(form)

    assert errors == {}
    assert data.settings.telegram_proxy_url == proxy_url


@pytest.mark.parametrize("proxy_url", ("proxy.example", "ftp://proxy.example", "http://:8080"))
def test_validate_settings_form_rejects_invalid_telegram_proxy_urls(proxy_url):
    form = _form()
    form["telegram_proxy_url"] = proxy_url

    _, errors = validate_settings_form(form)

    assert "telegram_proxy_url" in errors


def test_settings_saves_and_renders_notification_interval_and_telegram_proxy(client, settings):
    data = _form()
    data["error_notification_cooldown"] = "2h"
    data["telegram_proxy_url"] = "socks5h://proxy.example:1080"

    response = client.post("/settings", data=data)

    assert response.status_code == 302
    saved = json.loads(Path(settings.RUNTIME_CONFIG_PATH).read_text(encoding="utf-8"))
    assert saved["settings"]["error_notification_cooldown_seconds"] == 7200
    assert saved["settings"]["telegram_proxy_url"] == "socks5h://proxy.example:1080"

    reloaded = client.get("/settings")
    assert 'name="error_notification_cooldown" value="2h"' in reloaded.text
    assert 'name="telegram_proxy_url" value="socks5h://proxy.example:1080"' in reloaded.text


def test_settings_saves_and_renders_batching_configuration(client, settings):
    data = _form()
    data.update(
        {
            "batch_max_comments": "5",
            "batch_wait_hours": "6",
            "batch_retry_cooldown_minutes": "30",
            "batch_max_attempts_per_comment": "3",
        }
    )

    response = client.post("/settings", data=data)

    assert response.status_code == 302
    saved = json.loads(Path(settings.RUNTIME_CONFIG_PATH).read_text(encoding="utf-8"))
    assert saved["settings"]["batch_replies_enabled"] is True
    assert saved["settings"]["batch_cutover_at"] == "2026-08-30T12:00:00+03:00"
    assert saved["settings"]["batch_max_comments"] == 5
    assert saved["settings"]["batch_wait_hours"] == 6
    assert saved["settings"]["batch_retry_cooldown_minutes"] == 30
    assert saved["settings"]["batch_max_attempts_per_comment"] == 3

    reloaded = client.get("/settings")
    assert 'name="batch_replies_enabled" checked' in reloaded.text
    assert 'name="batch_max_comments" value="5"' in reloaded.text


def test_batching_requires_a_timezone_aware_cutover_when_enabled():
    data = _form()
    data["batch_cutover_at"] = "2026-08-30T12:00:00"

    _, errors = validate_settings_form(data)

    assert "batch_cutover_at" in errors


def test_settings_rejects_invalid_proxy_without_losing_form_values(client):
    data = _form()
    data["error_notification_cooldown"] = "15m"
    data["telegram_proxy_url"] = "ftp://proxy.example"

    response = client.post("/settings", data=data)

    assert response.status_code == 200
    assert "Введите URL proxy" in response.text
    assert 'name="error_notification_cooldown" value="15m"' in response.text
    assert 'name="telegram_proxy_url" value="ftp://proxy.example"' in response.text


def test_guest_settings_redirects_to_login(settings):
    client = TestClient(create_app(settings), follow_redirects=False)

    response = client.get("/settings")

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_authenticated_admin_can_open_vnc(client, settings):
    fake_vnc = FakeVncAccess(False)
    client = TestClient(create_app(settings, vnc_access=fake_vnc), follow_redirects=False)
    client.post("/login", data={"password": PASSWORD})

    response = client.post("/settings/vnc-access", data={"action": "open"})

    assert response.status_code == 302
    assert response.headers["location"] == "/settings?vnc=opened"
    assert fake_vnc.set_calls == [True]


def test_guest_cannot_toggle_vnc(settings):
    response = TestClient(create_app(settings), follow_redirects=False).post(
        "/settings/vnc-access", data={"action": "open"}
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_settings_renders_closed_vnc_state_and_open_action(settings):
    fake_vnc = FakeVncAccess(False)
    client = TestClient(create_app(settings, vnc_access=fake_vnc), follow_redirects=False)
    client.post("/login", data={"password": PASSWORD})

    response = client.get("/settings")

    assert "VNC закрыт" in response.text
    assert "Открыть VNC" in response.text


def test_settings_renders_open_vnc_state_and_close_action(settings):
    fake_vnc = FakeVncAccess(True)
    client = TestClient(create_app(settings, vnc_access=fake_vnc), follow_redirects=False)
    client.post("/login", data={"password": PASSWORD})

    response = client.get("/settings")

    assert "VNC открыт" in response.text
    assert "Закрыть VNC" in response.text


def test_settings_page_renders_runtime_values_and_only_readonly_vnc(client):
    response = client.get("/settings")

    assert response.status_code == 200
    assert 'name="max_comment_age_days" value="14"' in response.text
    assert 'name="max_reply_length" value="450"' in response.text
    assert 'name="developer_telegram_chat_ids" value="123"' in response.text
    assert 'name="developer_telegram_chat_ids" value="456"' in response.text
    assert 'name="role"' in response.text
    assert "community manager" in response.text
    assert 'name="vnc_host"' not in response.text
    assert 'value="vnc.example.test"' in response.text
    assert 'value="5901"' in response.text
    assert 'value="vnc-only-secret"' in response.text
    assert "readonly" in response.text
    assert "db-secret" not in response.text
    assert "TELEGRAM_PROXY_URL" not in response.text
    assert "DATABASE_URL" not in response.text


def test_settings_page_renders_cta_text_input(client):
    response = client.get("/settings")

    assert '<input type="text" name="cta_link"' in response.text
    assert 'value="https://saved.example/remont"' in response.text


def test_cta_interval_and_hourly_limit_persist_through_hot_reload(client, settings):
    data = _form()
    data["cta_every_n_comments"] = "7"
    data["max_comments_per_hour"] = "100"

    response = client.post("/settings", data=data)

    assert response.status_code == 302
    saved = json.loads(Path(settings.RUNTIME_CONFIG_PATH).read_text(encoding="utf-8"))
    assert saved["settings"]["cta_every_n_comments"] == 7
    assert saved["settings"]["max_comments_per_hour"] == 100
    reloaded = client.get("/settings")
    assert "Интервал CTA в комментариях" in reloaded.text
    assert "Максимум комментариев в час" in reloaded.text
    assert 'name="cta_every_n_comments" value="7"' in reloaded.text
    assert 'name="max_comments_per_hour" value="100"' in reloaded.text


@pytest.mark.parametrize("field", ("cta_every_n_comments", "max_comments_per_hour"))
@pytest.mark.parametrize("value", ("0", "-1", "1.5"))
def test_cta_interval_and_hourly_limit_reject_non_positive_integers(field, value):
    form = _form()
    form[field] = value

    _, errors = validate_settings_form(form)

    assert field in errors


def test_settings_prompt_fields_split_into_two_columns(client):
    response = client.get("/settings")

    assert 'class="prompt-columns"' in response.text
    assert "prompt-column-left" in response.text
    assert "prompt-column-right" in response.text

    left = response.text.split("prompt-column-left", 1)[1].split("prompt-column-right", 1)[0]
    right = response.text.split("prompt-column-right", 1)[1]
    for name in ("role", "tone_of_voice", "anti_rules", "task_lead"):
        assert f'name="{name}"' in left
    for name in ("task_engage", "cta_marker", "cta_link", "language"):
        assert f'name="{name}"' in right


def test_settings_renders_repeatable_lists_with_add_and_remove(client):
    response = client.get("/settings")

    for name, values in (
        ("developer_telegram_chat_ids", ("123", "456")),
        ("error_email_list", ("ops@example.com", "dev@example.com")),
    ):
        block = response.text.split(f'data-repeatable="{name}"', 1)[1].split("</fieldset>", 1)[0]
        for value in values:
            assert f'name="{name}" value="{value}"' in block
        assert block.count("repeatable-remove") >= len(values)
        assert "repeatable-add" in block


def test_multiple_list_values_saved_as_single_csv_string(client, settings):
    data = _form()
    data["developer_telegram_chat_ids"] = ["111", "222"]

    response = client.post("/settings", data=data)
    assert response.status_code == 302

    saved = json.loads(Path(settings.RUNTIME_CONFIG_PATH).read_text(encoding="utf-8"))
    stored = saved["settings"]["developer_telegram_chat_ids"]
    assert stored == "111, 222"
    # telegram_notifier reads the stored string back the same way.
    assert [c.strip() for c in stored.split(",") if c.strip()] == ["111", "222"]


def test_validate_settings_form_rejects_invalid_list_item():
    bad_telegram = _form()
    bad_telegram["developer_telegram_chat_ids"] = ["111", "nope"]
    _, errors = validate_settings_form(bad_telegram)
    assert "developer_telegram_chat_ids" in errors

    bad_email = _form()
    bad_email["error_email_list"] = ["ok@example.com", "broken"]
    _, errors = validate_settings_form(bad_email)
    assert "error_email_list" in errors

    good = _form()
    good["developer_telegram_chat_ids"] = ["111", "222"]
    good["error_email_list"] = ["one@example.com", "two@example.com"]
    data, errors = validate_settings_form(good)
    assert errors == {}
    assert data.settings.developer_telegram_chat_ids == "111, 222"
    assert data.settings.error_email_list == "one@example.com, two@example.com"


def test_valid_cta_link_persists_through_hot_reload(client):
    data = _form()
    data["cta_link"] = "https://persisted.example/remont"

    response = client.post("/settings", data=data)
    assert response.status_code == 302

    reloaded = client.get("/settings")
    assert 'value="https://persisted.example/remont"' in reloaded.text


def test_validate_settings_form_requires_nonempty_cta_text():
    empty = _form()
    empty["cta_link"] = ""
    _, errors = validate_settings_form(empty)
    assert "cta_link" in errors


def test_validate_settings_form_accepts_plain_cta_text():
    form = _form()
    form["cta_link"] = "domeo ru"

    data, errors = validate_settings_form(form)

    assert errors == {}
    assert data.prompt.cta_link == "domeo ru"


def test_settings_page_has_responsive_layout_hooks(client):
    response = client.get("/settings")

    assert 'class="settings-grid"' in response.text
    assert 'class="settings-column settings-column-bot"' in response.text
    assert 'class="settings-column settings-column-prompt"' in response.text
    assert 'class="settings-column settings-column-vnc"' in response.text
    assert 'class="checkbox-row"' in response.text


def test_settings_checkbox_keeps_its_compact_native_width():
    stylesheet = (
        Path(__file__).parents[2] / "dzen_commenter" / "admin" / "static" / "style.css"
    ).read_text(encoding="utf-8")

    assert ".settings-form input[type=\"checkbox\"] { width: auto; }" in stylesheet


def test_settings_switches_to_one_column_by_tablet_width():
    stylesheet = (
        Path(__file__).parents[2] / "dzen_commenter" / "admin" / "static" / "style.css"
    ).read_text(encoding="utf-8")

    assert "@media (max-width: 1024px)" in stylesheet
    assert "@media (max-width: 860px)" not in stylesheet


def test_valid_settings_post_saves_atomically_and_shows_success(client, settings, monkeypatch):
    import dzen_commenter.config.runtime_config as runtime_config_module

    replace_calls = []
    real_replace = runtime_config_module.os.replace

    def spy_replace(source, destination):
        replace_calls.append((source, destination))
        return real_replace(source, destination)

    monkeypatch.setattr(runtime_config_module.os, "replace", spy_replace)

    response = client.post("/settings", data=_form())

    assert response.status_code == 302
    assert response.headers["location"] == "/settings?saved=1"
    assert replace_calls
    saved = json.loads(Path(settings.RUNTIME_CONFIG_PATH).read_text(encoding="utf-8"))
    assert saved["settings"]["max_reply_length"] == 600
    assert saved["prompt"]["role"] == "new role"

    success = client.get(response.headers["location"])
    assert "Сохранено" in success.text


def test_invalid_settings_post_keeps_file_unchanged(client, settings, monkeypatch):
    import dzen_commenter.config.runtime_config as runtime_config_module

    path = settings.RUNTIME_CONFIG_PATH
    before = Path(path).read_text(encoding="utf-8")
    replace_calls = []
    monkeypatch.setattr(
        runtime_config_module.os,
        "replace",
        lambda source, destination: replace_calls.append((source, destination)),
    )

    data = _form()
    data.update(
        max_comment_age_days="-1",
        developer_telegram_chat_ids=["111", "not-a-telegram-id"],
        error_email_list=["ok@example.com", "broken-email"],
    )
    response = client.post("/settings", data=data)

    assert response.status_code == 200
    assert "error" in response.text.lower()
    assert Path(path).read_text(encoding="utf-8") == before
    assert replace_calls == []
