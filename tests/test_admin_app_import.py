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


def test_admin_delegates_migrations_to_app_via_health_gate():
    # Per decision-14-production-safety-gates (#3): migration ownership lives on
    # `app` only, gated by an `alembic current --check-heads` healthcheck, while
    # `admin` runs only uvicorn and waits for `app` to become healthy.
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    app_section = compose.split("  app:\n", 1)[1].split("  admin:\n", 1)[0]
    admin_section = compose.split("  admin:\n", 1)[1].split("  postgres:\n", 1)[0]

    # app owns the migration gate via its healthcheck.
    assert "alembic current --check-heads" in app_section

    # admin runs no alembic itself.
    assert "alembic" not in admin_section

    # admin only starts once app is healthy.
    depends_on = admin_section.split("depends_on:", 1)[1]
    assert "app:" in depends_on
    assert "condition: service_healthy" in depends_on
