from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile


def write_bot_health(
    path: str,
    *,
    cycle_succeeded: bool,
    authenticated: bool,
    now: datetime | None = None,
) -> None:
    heartbeat = now or datetime.now(timezone.utc)
    payload = {
        "heartbeat_at": heartbeat.astimezone(timezone.utc).isoformat(),
        "cycle_succeeded": bool(cycle_succeeded),
        "authenticated": bool(authenticated),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary_path, target)
    except BaseException:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def get_bot_health(
    path: str, poll_interval: float, *, now: datetime | None = None
) -> dict:
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": "starting", "heartbeat_at": None}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"status": "degraded", "heartbeat_at": None}

    if not isinstance(raw, dict):
        return {"status": "degraded", "heartbeat_at": None}
    heartbeat_text = raw.get("heartbeat_at")
    cycle_succeeded = raw.get("cycle_succeeded")
    authenticated = raw.get("authenticated")
    if (
        not isinstance(heartbeat_text, str)
        or not isinstance(cycle_succeeded, bool)
        or not isinstance(authenticated, bool)
    ):
        return {"status": "degraded", "heartbeat_at": None}

    try:
        heartbeat = datetime.fromisoformat(heartbeat_text)
        if heartbeat.tzinfo is None:
            return {"status": "degraded", "heartbeat_at": None}
    except ValueError:
        return {"status": "degraded", "heartbeat_at": None}

    current = now or datetime.now(timezone.utc)
    threshold = timedelta(seconds=max(3 * poll_interval, 600))
    if current.astimezone(timezone.utc) - heartbeat.astimezone(timezone.utc) > threshold:
        status = "stale"
    elif authenticated is False:
        status = "authentication_required"
    elif not cycle_succeeded or authenticated is not True:
        status = "degraded"
    else:
        status = "operational"
    return {"status": status, "heartbeat_at": heartbeat.isoformat()}


def main() -> int:
    status = get_bot_health(
        os.environ.get("BOT_HEALTH_PATH", "bot_health.json"),
        float(os.environ.get("POLL_INTERVAL", "60")),
    )["status"]
    return 0 if status == "operational" else 1


if __name__ == "__main__":
    sys.exit(main())
