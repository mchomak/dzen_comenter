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


def test_admin_command_applies_migrations_before_starting_uvicorn():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    admin_section = compose.split("  admin:\n", 1)[1].split("  postgres:\n", 1)[0]

    assert "alembic upgrade head" in admin_section
    assert admin_section.index("alembic upgrade head") < admin_section.index("uvicorn")
