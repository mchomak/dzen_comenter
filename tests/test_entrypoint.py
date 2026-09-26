import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def _shell_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    drive, tail = os.path.splitdrive(str(path))
    return f"/mnt/{drive[0].lower()}{tail.replace(chr(92), '/') }"


def test_headless_entrypoint_skips_display_and_vnc_helpers(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is not installed")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("alembic", "Xvfb", "xdpyinfo", "x11vnc", "websockify"):
        fake_command = fake_bin / command
        fake_command.write_text(
            '#!/bin/sh\nprintf "called:%s\\n" "$(basename "$0")"\n',
            encoding="utf-8",
            newline="\n",
        )
        fake_command.chmod(0o755)

    env = os.environ.copy()
    if os.name == "nt":
        fake_path = f"{_shell_path(fake_bin)}:/usr/bin:/bin"
        shell_command = (
            f'export PATH="{fake_path}" HEADLESS=true; '
            f'exec "{_shell_path(ROOT / "docker" / "entrypoint.sh")}" '
            'bash -c "exit 7"'
        )
    else:
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        shell_command = (
            f'exec "{ROOT / "docker" / "entrypoint.sh"}" bash -c "exit 7"'
        )
    env["HEADLESS"] = "true"
    env.pop("DISPLAY", None)
    result = subprocess.run(
        [bash, "-c", shell_command],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 7, "entrypoint did not return the app process status"
    assert "called:alembic" in result.stdout
    assert "called:Xvfb" not in result.stdout
    assert "called:xdpyinfo" not in result.stdout
    assert "called:x11vnc" not in result.stdout
    assert "called:websockify" not in result.stdout
