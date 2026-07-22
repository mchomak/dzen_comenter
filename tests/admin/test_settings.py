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


def _runtime_data() -> RuntimeConfigData:
    return RuntimeConfigData(
        settings=RuntimeSettings(
            auto_publish=True,
            max_comment_age_days=14,
            max_reply_length=450,
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
        "developer_telegram_chat_ids": ["111", "222"],
        "error_email_list": ["one@example.com", "two@example.com"],
        "role": "new role",
        "tone_of_voice": "new tone",
        "anti_rules": "new rules",
        "task_lead": "new lead task",
        "task_engage": "new engage task",
        "cta_marker": "new cta",
        "cta_link": "https://new.example/remont",
        "language": "ru",
    }


def test_guest_settings_redirects_to_login(settings):
    client = TestClient(create_app(settings), follow_redirects=False)

    response = client.get("/settings")

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


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


def test_settings_page_renders_cta_link_input(client):
    response = client.get("/settings")

    assert 'name="cta_link"' in response.text
    assert 'type="url"' in response.text
    assert 'value="https://saved.example/remont"' in response.text


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


def test_validate_settings_form_cta_link():
    empty = _form()
    empty["cta_link"] = ""
    _, errors = validate_settings_form(empty)
    assert "cta_link" in errors

    bad = _form()
    bad["cta_link"] = "l.domeo.ru/remont"
    _, errors = validate_settings_form(bad)
    assert "cta_link" in errors

    good = _form()
    good["cta_link"] = "https://valid.example/remont"
    data, errors = validate_settings_form(good)
    assert errors == {}
    assert data.prompt.cta_link == "https://valid.example/remont"


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
