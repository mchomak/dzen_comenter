import pathlib

from dzen_commenter.config.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# 14 новых полей stage-02 и их ожидаемые типы.
NEW_STR_FIELDS = {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DEVELOPER_TELEGRAM_CHAT_ID",
    "TELEGRAM_PROXY_URL",
    "EMAIL_FALLBACK_LIST",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "PROMPT_CONFIG_PATH",
    "VNC_PASSWORD",
    "DZEN_LOGIN_PHONE",
    "DZEN_LOGIN_PASSWORD",
}
NEW_INT_FIELDS = {
    "SMTP_PORT",
    "MAX_COMMENT_AGE_DAYS",
    "MAX_REPLY_LENGTH",
    "VNC_PORT",
    "DZEN_LOGIN_TIMEOUT_MS",
}
NEW_FIELDS = NEW_STR_FIELDS | NEW_INT_FIELDS


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
    assert env_keys == set(Settings.model_fields)
