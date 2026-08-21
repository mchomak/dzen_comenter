"""One-shot local control channel for changing the Dzen account."""

import json
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


_MAX_MESSAGE_BYTES = 16 * 1024
_SOCKET_TIMEOUT_SECONDS = 3.0


class DzenLoginControlUnavailable(Exception):
    """The bot process cannot be reached through its local control socket."""


class _AccountChanger(Protocol):
    def change_account(self, login: str, password: str) -> bool: ...


class DzenLoginControlClient:
    """Send credentials once to the bot process without persisting them."""

    def __init__(
        self,
        socket_path: str,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        *,
        timeout_seconds: float = _SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        self._socket_path = socket_path
        self._socket_factory = socket_factory
        self._timeout_seconds = timeout_seconds

    def login(self, login: str, password: str) -> bool:
        if not login.strip() or not password:
            raise ValueError("login and password are required")

        connection = None
        try:
            connection = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self._timeout_seconds)
            connection.connect(self._socket_path)
            _send_message(
                connection,
                {"action": "login", "login": login, "password": password},
            )
            response = _receive_message(connection)
        except (OSError, TimeoutError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise DzenLoginControlUnavailable("Dzen login controller is unavailable") from exc
        finally:
            if connection is not None:
                connection.close()

        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise DzenLoginControlUnavailable("Dzen login controller is unavailable")
        return response["ok"]


class DzenLoginControlServer:
    """Run the password-bearing side of the local Unix-socket protocol."""

    def __init__(
        self,
        socket_path: str,
        account_changer: _AccountChanger,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._account_changer = account_changer
        self._socket_factory = socket_factory
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def serve_in_thread(self) -> threading.Thread:
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self.serve_forever, daemon=True)
            self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop_event.set()

    def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        listener = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._socket_path))
            listener.listen()
            listener.settimeout(0.2)
            while not self._stop_event.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise
                with connection:
                    connection.settimeout(_SOCKET_TIMEOUT_SECONDS)
                    self._handle_connection(connection)
        finally:
            listener.close()
            self._socket_path.unlink(missing_ok=True)

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            request = _receive_message(connection)
            valid_request = (
                isinstance(request, dict)
                and request.get("action") == "login"
                and isinstance(request.get("login"), str)
                and isinstance(request.get("password"), str)
                and bool(request["login"].strip())
                and bool(request["password"])
            )
            if not valid_request:
                _send_message(connection, {"ok": False, "error": "Invalid login request"})
                return
            changed = self._account_changer.change_account(
                request["login"], request["password"]
            )
            _send_message(connection, {"ok": bool(changed)})
        except (OSError, TimeoutError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _send_message(connection, {"ok": False, "error": "Invalid login request"})
        except Exception:
            _send_message(connection, {"ok": False, "error": "Login failed"})


def _send_message(connection: socket.socket, message: dict[str, object]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_MESSAGE_BYTES:
        raise ValueError("message is too large")
    connection.sendall(payload + b"\n")


def _receive_message(connection: socket.socket) -> object:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = connection.recv(min(4096, _MAX_MESSAGE_BYTES + 1 - len(data)))
        if not chunk:
            raise ValueError("incomplete message")
        data.extend(chunk)
        if len(data) > _MAX_MESSAGE_BYTES:
            raise ValueError("message is too large")
    return json.loads(data[:-1].decode("utf-8"))
