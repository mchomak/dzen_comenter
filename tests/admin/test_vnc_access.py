import json
from pathlib import Path

import pytest

from dzen_commenter.admin.vnc_access import VncAccessClient, VncAccessUnavailable


class FakeSocket:
    def __init__(self, response: bytes = b'{"enabled": true}\n'):
        self.response = response
        self.timeout = None
        self.path = None
        self.sent = b""
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, path):
        self.path = path

    def sendall(self, data):
        self.sent = data

    def recv(self, size):
        response, self.response = self.response, b""
        return response

    def close(self):
        self.closed = True


def test_vnc_client_sends_one_json_action_line_and_reads_enabled_response():
    connection = FakeSocket()
    client = VncAccessClient("/run/test-vnc.sock", socket_factory=lambda *_: connection)

    assert client.status() is True
    assert connection.path == "/run/test-vnc.sock"
    assert connection.timeout == 2
    assert json.loads(connection.sent) == {"action": "status"}
    assert connection.sent.endswith(b"\n")
    assert connection.closed is True


def test_vnc_client_raises_unavailable_for_malformed_controller_response():
    client = VncAccessClient(
        "/run/test-vnc.sock",
        socket_factory=lambda *_: FakeSocket(b'{"error": "bad"}\n'),
    )

    with pytest.raises(VncAccessUnavailable):
        client.set_enabled(False)


def test_admin_compose_mounts_only_vnc_control_socket():
    compose = Path(__file__).parents[2] / "docker-compose.yml"
    admin = compose.read_text(encoding="utf-8").split("  admin:\n", 1)[1].split("\n  postgres:", 1)[0]

    assert "/run/dzen-vnc-control.sock:/run/dzen-vnc-control.sock" in admin
    assert "privileged" not in admin
    assert "NET_ADMIN" not in admin
    assert "/var/run/docker.sock" not in admin
