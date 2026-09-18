from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_migrations_have_one_linear_head():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0011_publication_claim_token"]
    assert script.get_revision("0011_publication_claim_token").down_revision == (
        "0010_reply_generation_queue"
    )


def test_migration_revision_ids_fit_alembic_version_column():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert all(len(revision.revision) <= 32 for revision in script.walk_revisions())
