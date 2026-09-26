import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def _resolved_compose_config() -> dict:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is not installed")

    env = os.environ | {
        "POSTGRES_USER": "compose-test-user",
        "POSTGRES_PASSWORD": "compose-test-password",
        "POSTGRES_DB": "compose_test_db",
        "HEADLESS": "true",
    }
    result = subprocess.run(
        [docker, "compose", "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "docker compose config failed"
    return json.loads(result.stdout)


def test_compose_does_not_publish_postgres_or_browser_ports():
    services = _resolved_compose_config()["services"]

    assert not services["postgres"].get("ports")
    assert not services["app"].get("ports")
    admin_ports = services["admin"].get("ports", [])
    assert len(admin_ports) == 1
    assert admin_ports[0]["published"] == "8080"


def test_compose_builds_internal_database_url_from_postgres_environment():
    services = _resolved_compose_config()["services"]

    assert "POSTGRES_USER" in services["postgres"]["environment"]
    assert "POSTGRES_PASSWORD" in services["postgres"]["environment"]
    assert "POSTGRES_DB" in services["postgres"]["environment"]
    assert "@postgres:5432/compose_test_db" in services["app"]["environment"]["DATABASE_URL"]
    assert "@postgres:5432/compose_test_db" in services["admin"]["environment"]["DATABASE_URL"]
    assert services["app"]["environment"]["HEADLESS"] == "true"
