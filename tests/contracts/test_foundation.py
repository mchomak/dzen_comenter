import ast
import pathlib
import typing

import pytest

import dzen_commenter  # noqa: F401
import dzen_commenter.config  # noqa: F401
import dzen_commenter.contracts  # noqa: F401
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts import interfaces
from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.models import Comment, Publication, Reply  # noqa: F401

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
INTERFACES_PATH = pathlib.Path(interfaces.__file__)

EXPECTED_FIELDS = {
    "DATABASE_URL",
    "AI_PROVIDER",
    "AI_MODEL",
    "AI_API_KEY",
    "AI_BASE_URL",
    "AI_TEMPERATURE",
    "AI_MAX_TOKENS",
    "AI_PROMPT_LANGUAGE",
    "GIGACHAT_AUTH_KEY",
    "GIGACHAT_SCOPE",
    "GIGACHAT_OAUTH_URL",
    "GIGACHAT_BASE_URL",
    "GIGACHAT_MODEL",
    "GIGACHAT_VERIFY_SSL_CERTS",
    "GIGACHAT_CA_BUNDLE",
    "USER_DATA_DIR",
    "STORAGE_STATE_PATH",
    "HEADLESS",
    "COMMENTS_URL",
    "DZEN_LOGIN_PHONE",
    "DZEN_LOGIN_PASSWORD",
    "DZEN_LOGIN_TIMEOUT_MS",
    "POLL_INTERVAL",
    "KEEPALIVE_INTERVAL",
    "AUTO_PUBLISH",
    "MAX_REPLIES_PER_CYCLE",
    # stage-02 config extension
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DEVELOPER_TELEGRAM_CHAT_ID_LIST",
    "TELEGRAM_PROXY_URL",
    "EMAIL_FALLBACK_LIST",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "MAX_COMMENT_AGE_DAYS",
    "MAX_REPLY_LENGTH",
    "PROMPT_CONFIG_PATH",
    "RUNTIME_CONFIG_PATH",
    "VNC_PORT",
    "VNC_PASSWORD",
    "NOVNC_PORT",
}

INTERFACE_NAMES = [
    "CommentRepository",
    "AIProvider",
    "PromptBuilder",
    "SessionManager",
    "DzenPage",
    "Notifier",
]


def test_enum_values():
    assert CommentStatus.NEW.value == "new"
    assert ReplyStatus.PUBLISHED.value == "published"
    assert {s.value for s in CommentStatus} == {"new", "answered", "skipped", "error"}
    assert {s.value for s in ReplyStatus} == {
        "generated",
        "published",
        "error",
        "skipped",
    }


def test_interfaces_exported():
    for name in INTERFACE_NAMES:
        assert hasattr(interfaces, name), f"missing interface {name}"


@pytest.mark.parametrize("name", INTERFACE_NAMES)
def test_interface_is_protocol(name):
    cls = getattr(interfaces, name)
    assert getattr(cls, "_is_protocol", False) is True


def test_interfaces_all_synchronous():
    source = INTERFACES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    async_defs = [
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    ]
    assert async_defs == [], "interfaces must be fully synchronous"
    assert "async def" not in source


def test_settings_reads_full_env(monkeypatch):
    # Не подмешивать реальный процессный env поверх .env.example.
    for field in EXPECTED_FIELDS:
        monkeypatch.delenv(field, raising=False)

    s = Settings(_env_file=str(ENV_EXAMPLE))
    for field in EXPECTED_FIELDS:
        assert hasattr(s, field), f"Settings missing field {field}"
    assert isinstance(s.AI_TEMPERATURE, float)
    assert isinstance(s.AI_MAX_TOKENS, int)
    assert isinstance(s.HEADLESS, bool)
    assert isinstance(s.POLL_INTERVAL, int)


def test_settings_fields_match_model():
    assert set(Settings.model_fields) == EXPECTED_FIELDS


def test_auto_publish_defaults_false():
    assert Settings.model_fields["AUTO_PUBLISH"].default is False


def _parse_env_keys(path: pathlib.Path) -> set[str]:
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_keys_match_settings():
    env_keys = _parse_env_keys(ENV_EXAMPLE)
    assert env_keys == EXPECTED_FIELDS == set(Settings.model_fields)


def test_prompt_context_reply_type_literal():
    assert typing.get_args(interfaces.ReplyType) == ("lead", "engage")
