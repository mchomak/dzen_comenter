import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]


def _drop_all(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS replies CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS comments CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS publications CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to a clean Postgres")

    eng = create_engine(url)

    # Clean DB, then apply migrations the same way acceptance #1 requires.
    _drop_all(eng)

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "dzen_commenter" / "db" / "migrations"))
    command.upgrade(cfg, "head")

    yield eng

    _drop_all(eng)
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_rows(engine):
    """Truncate data between tests so each test sees an empty schema."""
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE replies, comments, publications RESTART IDENTITY CASCADE")
        )
    yield
