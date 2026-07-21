import os
import subprocess
import sys


def test_admin_app_import_does_not_require_database_driver_at_import_time():
    env = os.environ | {"DATABASE_URL": "postgresql://user:password@localhost/database"}

    result = subprocess.run(
        [sys.executable, "-c", "import dzen_commenter.admin.app"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
