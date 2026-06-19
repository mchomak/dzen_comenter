import ast
import json
import logging
from pathlib import Path

import dzen_commenter.monitoring
from dzen_commenter.monitoring import StructuredFormatter, configure_logging
from dzen_commenter.monitoring.logging_config import _HANDLER_MARKER


def _make_record(message, *, extra=None, exc_info=None):
    record = logging.LogRecord(
        name="dzen_commenter.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=exc_info,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


# Acceptance 4: структурный формат машиночитаем.
def test_format_is_json_with_required_keys():
    record = _make_record("привет мир", extra={"event": "notify"})
    out = StructuredFormatter().format(record)
    parsed = json.loads(out)
    assert {"timestamp", "level", "logger", "message"} <= parsed.keys()
    assert parsed["message"] == "привет мир"
    assert parsed["event"] == "notify"


def test_format_cyrillic_not_escaped():
    record = _make_record("сбой авторизации")
    out = StructuredFormatter().format(record)
    # ensure_ascii=False -> кириллица читаема как есть, без \uXXXX.
    assert "сбой авторизации" in out
    assert json.loads(out)["message"] == "сбой авторизации"


def test_format_includes_exception_when_present():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = _make_record("ошибка", exc_info=sys.exc_info())
    parsed = json.loads(StructuredFormatter().format(record))
    assert "exception" in parsed
    assert "RuntimeError" in json.dumps(parsed["exception"], ensure_ascii=False)


# Acceptance 5: configure_logging идемпотентна.
def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        configure_logging("INFO")
        configure_logging("INFO")
        module_handlers = [
            h for h in root.handlers if getattr(h, _HANDLER_MARKER, False)
        ]
        assert len(module_handlers) == 1
        assert isinstance(module_handlers[0].formatter, StructuredFormatter)
    finally:
        root.handlers[:] = original_handlers
        root.setLevel(original_level)


# Acceptance 6: чистота слоя — AST-скан импортов monitoring/.
FORBIDDEN_TOP = {
    "httpx",
    "psycopg",
    "sqlalchemy",
    "alembic",
    "playwright",
    "pydantic",
    "pydantic_settings",
}
FORBIDDEN_SUBPKG = {"db", "ai", "prompt", "browser", "dzen", "orchestrator"}


def _imported_modules(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.append(node.module)
    return names


def test_monitoring_layer_is_pure():
    pkg_dir = Path(dzen_commenter.monitoring.__file__).parent
    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for module in _imported_modules(tree):
            root = module.split(".")[0]
            assert root not in FORBIDDEN_TOP, f"{py_file.name} imports {module}"
            if root == "dzen_commenter":
                parts = module.split(".")
                sub = parts[1] if len(parts) > 1 else ""
                assert sub in ("contracts", "config", "monitoring", ""), (
                    f"{py_file.name} imports dzen_commenter.{sub}"
                )
                assert sub not in FORBIDDEN_SUBPKG
