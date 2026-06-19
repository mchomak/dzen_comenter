import ast
from pathlib import Path

import pytest

import dzen_commenter.prompt
from dzen_commenter.contracts.interfaces import PromptContext
from dzen_commenter.prompt import DameoPromptBuilder
from dzen_commenter.prompt.builder import CTA_MARKER

PUB_TITLE = "Как недорого обновить ванную комнату"
THREAD_TEXT = "Подскажите, с чего начать и сколько примерно закладывать бюджета?"


def make_context(reply_type):
    return PromptContext(
        publication_title=PUB_TITLE,
        thread_text=THREAD_TEXT,
        reply_type=reply_type,
    )


# Acceptance 1: экспорт и реализация контракта.
@pytest.mark.parametrize("reply_type", ["lead", "engage"])
def test_build_returns_nonempty_str(reply_type):
    builder = DameoPromptBuilder()
    assert hasattr(builder, "build")
    result = builder.build(make_context(reply_type))
    assert isinstance(result, str)
    assert len(result.strip()) > 0


# Acceptance 3: CTA только в lead.
def test_cta_only_in_lead():
    builder = DameoPromptBuilder()
    lead = builder.build(make_context("lead"))
    engage = builder.build(make_context("engage"))
    assert CTA_MARKER in lead
    assert CTA_MARKER not in engage


# Acceptance 4: контекст инжектится дословно.
@pytest.mark.parametrize("reply_type", ["lead", "engage"])
def test_context_injected_verbatim(reply_type):
    result = DameoPromptBuilder().build(make_context(reply_type))
    assert PUB_TITLE in result
    assert THREAD_TEXT in result


# Acceptance 5: анти-правила присутствуют в промпте.
@pytest.mark.parametrize("reply_type", ["lead", "engage"])
def test_anti_rules_present(reply_type):
    result = DameoPromptBuilder().build(make_context(reply_type)).lower()
    assert "без точной стоимости" in result
    assert "не отвечай токсично" in result
    assert "не спамь" in result


# Acceptance 7: язык по умолчанию — русский.
def test_default_language_is_russian():
    result = DameoPromptBuilder().build(make_context("lead"))
    assert "Dameo" in result
    assert any("а" <= ch.lower() <= "я" or ch == "ё" for ch in result)


# Acceptance 6: чистота слоя — AST-скан импортов prompt/.
FORBIDDEN_TOP = {"httpx", "psycopg", "sqlalchemy", "playwright", "pydantic"}
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
