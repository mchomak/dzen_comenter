from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from dzen_commenter.contracts.interfaces import Notifier


class DeveloperNotifier:
    """Guarded notification facade for developer-facing failures."""

    def __init__(
        self,
        transport: Notifier,
        *,
        error_cooldown_provider: Callable[[], int] | None = None,
        cooldown_state_path: str | None = None,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.transport = transport
        self._error_cooldown_provider = error_cooldown_provider
        self._cooldown_state_path = (
            Path(cooldown_state_path) if cooldown_state_path is not None else None
        )
        self._time_fn = time_fn
        self._lock = Lock()
        self._pending_error_notifications: set[str] = set()
        self._last_error_notifications = self._load_cooldown_state()

    def notify(self, message: str) -> None:
        try:
            self.transport.notify(message)
        except Exception:
            logging.getLogger(__name__).warning(
                "Developer notification delivery failed",
                exc_info=True,
            )

    def notify_error(self, message: str, error: Exception | None = None) -> None:
        signature = self._error_signature(message, error)
        if not self._should_send_error(signature):
            return
        try:
            self.transport.notify_error(message, error)
        except Exception:
            logging.getLogger(__name__).warning(
                "Developer error notification delivery failed",
                exc_info=True,
            )
        else:
            self._record_error_notification(signature)
        finally:
            with self._lock:
                self._pending_error_notifications.discard(signature)

    def _should_send_error(self, signature: str) -> bool:
        cooldown = (
            self._error_cooldown_provider()
            if self._error_cooldown_provider is not None
            else 0
        )
        now = self._time_fn()
        with self._lock:
            if signature in self._pending_error_notifications:
                return False
            last_sent = self._last_error_notifications.get(signature)
            if (
                self._error_cooldown_provider is not None
                and last_sent is not None
                and now - last_sent < cooldown
            ):
                return False
            self._pending_error_notifications.add(signature)
        return True

    def _record_error_notification(self, signature: str) -> None:
        if self._error_cooldown_provider is None:
            return
        cooldown = self._error_cooldown_provider()
        now = self._time_fn()
        with self._lock:
            self._last_error_notifications[signature] = now
            self._drop_expired_notifications(now, cooldown)
            self._save_cooldown_state()

    @staticmethod
    def _error_signature(message: str, error: Exception | None) -> str:
        error_type = type(error).__qualname__ if error is not None else ""
        error_text = str(error) if error is not None else ""
        source = f"{message}\n{error_type}\n{error_text}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _load_cooldown_state(self) -> dict[str, float]:
        if self._cooldown_state_path is None:
            return {}
        try:
            raw = json.loads(self._cooldown_state_path.read_text(encoding="utf-8"))
            entries = raw.get("last_error_notifications", {})
            if not isinstance(entries, dict):
                raise TypeError("last_error_notifications must be an object")
            return {
                signature: float(sent_at)
                for signature, sent_at in entries.items()
                if isinstance(signature, str)
            }
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logging.getLogger(__name__).warning(
                "Failed to read developer notification cooldown state",
                exc_info=True,
            )
            return {}

    def _drop_expired_notifications(self, now: float, cooldown: int) -> None:
        self._last_error_notifications = {
            signature: sent_at
            for signature, sent_at in self._last_error_notifications.items()
            if now - sent_at < cooldown
        }

    def _save_cooldown_state(self) -> None:
        if self._cooldown_state_path is None:
            return
        path = self._cooldown_state_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"last_error_notifications": self._last_error_notifications},
                        handle,
                    )
                os.replace(tmp_name, path)
            except BaseException:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
                raise
        except OSError:
            logging.getLogger(__name__).warning(
                "Failed to save developer notification cooldown state",
                exc_info=True,
            )


class DeveloperNotificationHandler(logging.Handler):
    """Forward only ERROR and CRITICAL records without recursive logging."""

    def __init__(self, notifier: Notifier) -> None:
        super().__init__(level=logging.ERROR)
        self.notifier = notifier

    def emit(self, record: logging.LogRecord) -> None:
        error = record.exc_info[1] if record.exc_info else None
        self.notifier.notify_error(record.getMessage(), error)
