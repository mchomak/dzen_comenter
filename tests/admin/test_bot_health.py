import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dzen_commenter.admin.app import create_app
from dzen_commenter.admin.config import AdminSettings
from dzen_commenter.bot_health import get_bot_health, write_bot_health


@pytest.fixture
def health_client(tmp_path):
    health_path = tmp_path / "bot-health.json"
    settings = AdminSettings(
        _env_file=None,
        ADMIN_PASSWORD="test-password",
        ADMIN_SESSION_SECRET="test-session-secret",
        RUNTIME_CONFIG_PATH=str(tmp_path / "runtime.json"),
        BOT_HEALTH_PATH=str(health_path),
        POLL_INTERVAL=60,
    )
    return TestClient(create_app(settings)), health_path


def _write_snapshot(
    path,
    *,
    seconds_ago=0,
    cycle_succeeded=True,
    authenticated=True,
    cycle_in_progress=False,
):
    heartbeat = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    path.write_text(
        json.dumps(
            {
                "heartbeat_at": heartbeat.isoformat(),
                "cycle_succeeded": cycle_succeeded,
                "authenticated": authenticated,
                "cycle_in_progress": cycle_in_progress,
                "error": "must never be returned to clients",
            }
        ),
        encoding="utf-8",
    )


def test_bot_health_is_starting_until_a_snapshot_exists(health_client):
    client, _ = health_client

    response = client.get("/health/bot")

    assert response.status_code == 200
    assert response.json() == {"status": "starting", "heartbeat_at": None}


@pytest.mark.parametrize(
    ("snapshot", "expected_status", "heartbeat_present"),
    (
        ({"cycle_succeeded": True, "authenticated": True}, "operational", True),
        ({"cycle_succeeded": True, "authenticated": False}, "authentication_required", True),
        ({"cycle_succeeded": False, "authenticated": True}, "degraded", True),
        ({"cycle_succeeded": True, "authenticated": True, "seconds_ago": 300}, "operational", True),
        ({"cycle_succeeded": True, "authenticated": True, "seconds_ago": 601}, "stale", True),
        (
            {
                "cycle_succeeded": False,
                "authenticated": True,
                "cycle_in_progress": True,
                "seconds_ago": 300,
            },
            "in_progress",
            True,
        ),
        (
            {
                "cycle_succeeded": True,
                "authenticated": True,
                "cycle_in_progress": True,
                "seconds_ago": 601,
            },
            "stale",
            True,
        ),
    ),
)
def test_bot_health_derives_public_status_without_internal_details(
    health_client, snapshot, expected_status, heartbeat_present
):
    client, health_path = health_client
    _write_snapshot(health_path, **snapshot)

    response = client.get("/health/bot")

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert (response.json()["heartbeat_at"] is not None) is heartbeat_present
    assert set(response.json()) == {"status", "heartbeat_at"}
    assert "must never be returned" not in response.text


def test_corrupt_bot_health_never_reports_operational(health_client):
    client, health_path = health_client
    health_path.write_text("not json", encoding="utf-8")

    response = client.get("/health/bot")

    assert response.json() == {"status": "degraded", "heartbeat_at": None}


@pytest.mark.parametrize(("poll_interval", "freshness_limit"), ((60, 600), (10, 600), (250, 750)))
def test_shared_evaluator_uses_the_exact_freshness_limit(
    tmp_path, poll_interval, freshness_limit
):
    path = tmp_path / "bot-health.json"
    now = datetime(2026, 9, 27, 12, 1, 30, tzinfo=timezone.utc)
    heartbeat = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    path.write_text(
        json.dumps(
            {
                "heartbeat_at": heartbeat.isoformat(),
                "cycle_succeeded": True,
                "authenticated": True,
            }
        ),
        encoding="utf-8",
    )

    assert get_bot_health(
        path,
        poll_interval,
        now=heartbeat + timedelta(seconds=freshness_limit),
    ) == {
        "status": "operational",
        "heartbeat_at": "2026-09-27T12:00:00+00:00",
    }
    assert get_bot_health(
        path,
        poll_interval,
        now=heartbeat + timedelta(seconds=freshness_limit + 1),
    )["status"] == "stale"


def test_health_writer_replaces_snapshot_without_extra_fields_or_temp_files(tmp_path):
    path = tmp_path / "bot-health.json"
    path.write_text("old snapshot", encoding="utf-8")
    heartbeat = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)

    write_bot_health(
        str(path), cycle_succeeded=False, authenticated=False, now=heartbeat
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "heartbeat_at": "2026-09-27T12:00:00+00:00",
        "cycle_succeeded": False,
        "authenticated": False,
        "cycle_in_progress": False,
    }
    assert list(tmp_path.iterdir()) == [path]


def test_progress_writer_refreshes_heartbeat_and_preserves_last_cycle_result(tmp_path):
    from dzen_commenter.bot_health import write_bot_progress

    path = tmp_path / "bot-health.json"
    path.write_text(
        json.dumps(
            {
                "heartbeat_at": "2026-09-27T11:00:00+00:00",
                "cycle_succeeded": False,
                "authenticated": True,
                "cycle_in_progress": False,
            }
        ),
        encoding="utf-8",
    )
    heartbeat = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)

    write_bot_progress(str(path), now=heartbeat)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "heartbeat_at": "2026-09-27T12:00:00+00:00",
        "cycle_succeeded": False,
        "authenticated": True,
        "cycle_in_progress": True,
    }


def test_healthcheck_accepts_a_fresh_in_progress_cycle(tmp_path, monkeypatch):
    from dzen_commenter.bot_health import main

    path = tmp_path / "bot-health.json"
    _write_snapshot(
        path,
        cycle_succeeded=False,
        authenticated=True,
        cycle_in_progress=True,
    )
    monkeypatch.setenv("BOT_HEALTH_PATH", str(path))
    monkeypatch.setenv("POLL_INTERVAL", "60")

    assert main() == 0
