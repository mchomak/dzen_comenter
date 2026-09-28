import os
import subprocess
import sys
from pathlib import Path


def test_admin_app_import_does_not_require_database_driver_at_import_time():
    env = os.environ | {"DATABASE_URL": "postgresql://user:password@localhost/database"}

    result = subprocess.run(
        [sys.executable, "-c", "import dzen_commenter.admin.app"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_admin_starts_on_database_readiness_independently_of_bot_health():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (Path(__file__).parents[1] / "docker" / "entrypoint.sh").read_text(
        encoding="utf-8"
    )
    app_section = compose.split("  app:\n", 1)[1].split("  admin:\n", 1)[0]
    admin_section = compose.split("  admin:\n", 1)[1].split("\n  postgres:\n", 1)[0]

    # The app entrypoint applies migrations; its healthcheck means worker-ready.
    assert "alembic upgrade head" in entrypoint
    assert "dzen_commenter.bot_health" in app_section

    # admin runs no alembic itself.
    assert "alembic" not in admin_section

    # admin is independent of Dzen authentication and app readiness.
    assert "depends_on:\n      postgres:\n        condition: service_healthy" in admin_section
    assert "depends_on:\n      app:" not in admin_section
