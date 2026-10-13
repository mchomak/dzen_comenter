from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_migrations_have_one_linear_head():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0012_ai_call_reservations"]
    assert script.get_revision("0012_ai_call_reservations").down_revision == (
        "0011_publication_claim_token"
    )


def test_migration_revision_ids_fit_alembic_version_column():
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert all(len(revision.revision) <= 32 for revision in script.walk_revisions())
