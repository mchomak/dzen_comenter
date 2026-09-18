import ast
import pathlib
import typing

import pytest

import dzen_commenter  # noqa: F401
import dzen_commenter.config  # noqa: F401
import dzen_commenter.contracts  # noqa: F401
from dzen_commenter.config.runtime_config import RuntimeSettings
from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts import interfaces
from dzen_commenter.contracts.enums import (
    CommentStatus,
    PublicationFailureOutcome,
    ReplyStatus,
)
import dzen_commenter.contracts.models as contract_models
from dzen_commenter.contracts.models import (  # noqa: F401
    ArticleContext,
    ClaimedPublication,
    Comment,
    Publication,
    Reply,
)
from dzen_commenter.db.repository import PostgresCommentRepository

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
    "DZEN_LOGIN_CONTROL_SOCKET",
    "POLL_INTERVAL",
    "KEEPALIVE_INTERVAL",
    "MAX_REPLIES_PER_CYCLE",
    # stage-02 config extension
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_PROXY_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "RUNTIME_CONFIG_PATH",
    "ADMIN_PASSWORD",
    "ADMIN_SESSION_SECRET",
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
    assert {
        "new",
        "generating",
        "generated",
        "publishing",
        "published",
        "skipped",
        "generation_retry",
        "generation_error",
        "publication_retry",
        "publication_error",
    } <= {s.value for s in CommentStatus}
    assert {s.value for s in ReplyStatus} == {
        "generated",
        "published",
        "error",
        "skipped",
    }


def test_interfaces_exported():
    for name in INTERFACE_NAMES:
        assert hasattr(interfaces, name), f"missing interface {name}"


def test_comment_repository_contract_exposes_only_single_generation_operations():
    methods = set(interfaces.CommentRepository.__dict__)

    assert {
        "upsert_eligible_comment",
        "enqueue_pending_generations",
        "skip_comment_if_new",
        "enqueue_generation",
        "claim_next_generation",
        "complete_generation",
        "skip_generation",
        "fail_generation",
        "claim_next_publication",
        "complete_publication",
        "fail_publication",
    } <= methods
    assert not {
        "enqueue_batch_comment",
        "claim_next_batch",
        "save_batch_outcomes",
    } & methods


def test_postgres_repository_exposes_no_legacy_batch_operations():
    assert not {
        "enqueue_batch_comment",
        "claim_next_batch",
        "save_batch_outcomes",
    } & set(PostgresCommentRepository.__dict__)


def test_contract_models_expose_no_legacy_batch_types():
    assert not {
        "BatchItem",
        "ClaimedBatch",
        "BatchOutcome",
    } & set(vars(contract_models))


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


def test_runtime_auto_publish_defaults_false():
    assert RuntimeSettings().auto_publish is False


def test_runtime_generation_retry_defaults_are_safe():
    settings = RuntimeSettings()

    assert settings.generation_retry_cooldown_minutes == 60
    assert settings.generation_max_attempts_per_comment == 3


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


def test_publication_queue_contract_is_exposed():
    assert ArticleContext.__dataclass_fields__.keys() == {
        "publication_id",
        "text",
        "status",
        "fetched_at",
        "content_hash",
    }
    assert ClaimedPublication.__dataclass_fields__.keys() == {
        "reply_id",
        "comment",
        "text",
        "claim_token",
    }
    for method in (
        "get_article_context",
        "save_article_context",
        "enqueue_publication",
        "expire_stale_publications",
        "claim_next_publication",
        "complete_publication",
        "fail_publication",
    ):
        assert callable(getattr(interfaces.CommentRepository, method))
    assert {outcome.value for outcome in PublicationFailureOutcome} == {
        "retry",
        "terminal",
    }
