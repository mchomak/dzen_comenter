import ast
from pathlib import Path

import pytest

import dzen_commenter.prompt
from dzen_commenter.contracts.interfaces import PromptContext
from dzen_commenter.prompt import DameoPromptBuilder, PromptBrandConfig, load_brand_config
from dzen_commenter.prompt.config_loader import (
    DEFAULT_ANTI_RULES,
    DEFAULT_CTA_MARKER,
    DEFAULT_ROLE,
    DEFAULT_TASK_ENGAGE,
    DEFAULT_TASK_LEAD,
    DEFAULT_TONE_OF_VOICE,
)

PUB_TITLE = "publication title"
THREAD_TEXT = "thread text"


def make_context(reply_type):
    return PromptContext(
        publication_title=PUB_TITLE,
        thread_text=THREAD_TEXT,
        reply_type=reply_type,
    )


def _expected_default_prompt(task: str) -> str:
    return "\n\n".join(
        [
            DEFAULT_ROLE,
            DEFAULT_TONE_OF_VOICE,
            DEFAULT_ANTI_RULES,
            (
                "РљРѕРЅС‚РµРєСЃС‚:\n"
                f"РўРµРјР° РїСѓР±Р»РёРєР°С†РёРё: {PUB_TITLE}\n"
                f"Р’РµС‚РєР° РѕР±СЃСѓР¶РґРµРЅРёСЏ: {THREAD_TEXT}"
            ),
            task,
        ]
    )


@pytest.mark.parametrize("reply_type", ["lead", "engage"])
def test_build_returns_nonempty_str(reply_type):
    builder = DameoPromptBuilder()
    assert hasattr(builder, "build")
    result = builder.build(make_context(reply_type))
    assert isinstance(result, str)
    assert len(result.strip()) > 0


def test_default_prompt_matches_frozen_default_blocks():
    builder = DameoPromptBuilder()

    assert builder.build(make_context("lead")) == _expected_default_prompt(
        DEFAULT_TASK_LEAD
    )
    assert builder.build(make_context("engage")) == _expected_default_prompt(
        DEFAULT_TASK_ENGAGE
    )


def test_cta_only_in_lead():
    builder = DameoPromptBuilder()
    lead = builder.build(make_context("lead"))
    engage = builder.build(make_context("engage"))
    assert DEFAULT_CTA_MARKER in lead
    assert DEFAULT_CTA_MARKER not in engage


@pytest.mark.parametrize("reply_type", ["lead", "engage"])
def test_context_injected_verbatim(reply_type):
    result = DameoPromptBuilder().build(make_context(reply_type))
    assert PUB_TITLE in result
    assert THREAD_TEXT in result


@pytest.mark.parametrize("reply_type", ["lead", "engage"])
def test_anti_rules_present(reply_type):
    result = DameoPromptBuilder().build(make_context(reply_type))
    assert DEFAULT_ANTI_RULES in result


def test_default_language_is_russian():
    builder = DameoPromptBuilder()
    result = builder.build(make_context("lead"))
    assert builder.language == "ru"
    assert DEFAULT_ROLE in result


def test_prompt_config_exports_available():
    assert PromptBrandConfig is not None
    assert callable(load_brand_config)


def test_builder_missing_config_path_matches_default(tmp_path):
    context = make_context("lead")
    default = DameoPromptBuilder().build(context)
    missing = DameoPromptBuilder(config_path=str(tmp_path / "missing.json")).build(
        context
    )
    assert missing == default


def test_builder_uses_config_override(tmp_path):
    config_path = tmp_path / "prompt.json"
    config_path.write_text(
        """
        {
          "role": "custom role",
          "tone_of_voice": "custom tone",
          "anti_rules": "custom anti rules",
          "task_lead": "custom lead task with custom CTA",
          "task_engage": "custom engage task",
          "cta_marker": "custom CTA",
          "language": "en"
        }
        """,
        encoding="utf-8",
    )

    builder = DameoPromptBuilder(config_path=str(config_path))

    lead = builder.build(make_context("lead"))
    engage = builder.build(make_context("engage"))
    assert builder.language == "en"
    assert "custom role" in lead
    assert "custom tone" in lead
    assert "custom anti rules" in lead
    assert "custom lead task" in lead
    assert "custom CTA" in lead
    assert "custom engage task" in engage
    assert "custom CTA" not in engage


FORBIDDEN_TOP = {
    "httpx",
    "psycopg",
    "sqlalchemy",
    "playwright",
    "pydantic",
    "yaml",
}
FORBIDDEN_SUBPKG = {
    "config",
    "db",
    "ai",
    "browser",
    "dzen",
    "monitoring",
}


def _imported_modules(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.append(node.module)
    return names


def test_prompt_layer_is_pure():
    pkg_dir = Path(dzen_commenter.prompt.__file__).parent
    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for module in _imported_modules(tree):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_TOP, f"{py_file.name} imports {module}"
            if root == "dzen_commenter":
                parts = module.split(".")
                sub = parts[1] if len(parts) > 1 else ""
                assert sub in ("contracts", "prompt", ""), (
                    f"{py_file.name} imports dzen_commenter.{sub}"
                )
                assert sub not in FORBIDDEN_SUBPKG
