"""Restricted client for the root-owned VNC firewall controller."""

import json
import socket
from collections.abc import Callable


class VncAccessUnavailable(Exception):
    pass


class VncAccessClient:
    def __init__(
        self,
        socket_path: str,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ):
        self._socket_path = socket_path
        self._socket_factory = socket_factory

    def status(self) -> bool:
        return self._request("status")

    def set_enabled(self, enabled: bool) -> bool:
        return self._request("open" if enabled else "close")

    def _request(self, action: str) -> bool:
        connection = None
        try:
            connection = self._socket_factory(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM)
            connection.settimeout(2)
            connection.connect(self._socket_path)
            connection.sendall(json.dumps({"action": action}).encode() + b"\n")
            response = _read_response(connection)
            if not isinstance(response, dict) or type(response.get("enabled")) is not bool:
                raise ValueError("invalid controller response")
            return response["enabled"]
        except (OSError, TimeoutError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise VncAccessUnavailable("VNC access controller is unavailable") from exc
        finally:
            if connection is not None:
                connection.close()


def _read_response(connection: socket.socket) -> object:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = connection.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return json.loads(data.decode())
