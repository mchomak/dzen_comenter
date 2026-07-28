from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_migrations_have_one_linear_head():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0006_add_reply_article_context_status"]
    assert script.get_revision("0006_add_reply_article_context_status").down_revision == (
        "0005_store_moscow_time"
    )
