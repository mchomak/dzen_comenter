import pathlib

import pytest

from dzen_commenter.config.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# 14 новых полей stage-02 и их ожидаемые типы.
NEW_STR_FIELDS = {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_PROXY_URL",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "VNC_PASSWORD",
    "DZEN_LOGIN_PHONE",
    "DZEN_LOGIN_PASSWORD",
    "DZEN_LOGIN_CONTROL_SOCKET",
}
NEW_INT_FIELDS = {
    "SMTP_PORT",
    "VNC_PORT",
    "DZEN_LOGIN_TIMEOUT_MS",
    "MAX_ACTIVE_WORK_ITEMS",
}
NEW_FLOAT_FIELDS = {"AI_REQUEST_TIMEOUT_SECONDS"}
NEW_FIELDS = NEW_STR_FIELDS | NEW_INT_FIELDS | NEW_FLOAT_FIELDS


def _load_settings(monkeypatch) -> Settings:
    # Не подмешивать реальный процессный env поверх .env.example.
    for field in NEW_FIELDS:
        monkeypatch.delenv(field, raising=False)
    return Settings(_env_file=str(ENV_EXAMPLE))


def test_settings_has_all_new_fields():
    for field in NEW_FIELDS:
        assert field in Settings.model_fields, f"Settings missing new field {field}"


def test_settings_reads_new_fields_with_types(monkeypatch):
    s = _load_settings(monkeypatch)
    for field in NEW_STR_FIELDS:
        assert isinstance(getattr(s, field), str), f"{field} must be str"
    for field in NEW_INT_FIELDS:
        # bool — подкласс int в Python; исключаем его явно.
        value = getattr(s, field)
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"{field} must be int"
        )
    for field in NEW_FLOAT_FIELDS:
        assert isinstance(getattr(s, field), float), f"{field} must be float"


def _settings_without_env_file(**overrides) -> Settings:
    values = {
        "DATABASE_URL": "postgresql://example",
        "AI_PROVIDER": "openai",
        "AI_MODEL": "test-model",
        "AI_API_KEY": "test-key",
        "AI_BASE_URL": "https://example.test/v1",
        "AI_TEMPERATURE": 0.3,
        "AI_MAX_TOKENS": 32,
        "AI_PROMPT_LANGUAGE": "ru",
        "USER_DATA_DIR": "/tmp/user-data",
        "STORAGE_STATE_PATH": "/tmp/state.json",
        "HEADLESS": True,
        "COMMENTS_URL": "https://dzen.ru/",
        "POLL_INTERVAL": 60,
        "KEEPALIVE_INTERVAL": 300,
        "MAX_REPLIES_PER_CYCLE": 10,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_ai_timeout_and_work_window_settings_defaults():
    settings = _settings_without_env_file()

    assert settings.AI_REQUEST_TIMEOUT_SECONDS == 90
    assert settings.MAX_ACTIVE_WORK_ITEMS == 30


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("AI_REQUEST_TIMEOUT_SECONDS", 0),
        ("AI_REQUEST_TIMEOUT_SECONDS", -1),
        ("MAX_ACTIVE_WORK_ITEMS", 0),
        ("MAX_ACTIVE_WORK_ITEMS", -1),
    ],
)
def test_ai_timeout_and_work_window_settings_must_be_positive(field, value):
    with pytest.raises(ValueError):
        _settings_without_env_file(**{field: value})


def _parse_env_keys(path: pathlib.Path) -> set[str]:
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_contains_new_keys():
    env_keys = _parse_env_keys(ENV_EXAMPLE)
    assert NEW_FIELDS <= env_keys


def test_env_example_keys_match_settings_exactly():
    env_keys = _parse_env_keys(ENV_EXAMPLE)
    compose_only_fields = {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}
    assert env_keys == set(Settings.model_fields) | compose_only_fields
