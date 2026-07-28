"""Root-only Unix socket controller for the VNC Docker firewall rules."""

import json
import os
import socket
import subprocess
from collections.abc import Callable
from typing import Protocol


SOCKET_PATH = "/run/dzen-vnc-control.sock"
FIREWALLS = ("iptables", "ip6tables")
CHAIN = "DZEN_VNC"
PORTS = "5900,6080"


class CommandRunner(Protocol):
    def run(self, args: list[str]) -> int: ...


class VncFirewall:
    def __init__(self, runner: CommandRunner | None = None):
        self._run: Callable[[list[str]], int] = runner.run if runner else _run_command

    def set_enabled(self, enabled: bool) -> bool:
        for firewall in FIREWALLS:
            self._ensure_chain(firewall)
            self._run([firewall, "-F", CHAIN])
            if not enabled:
                self._run([firewall, "-A", CHAIN, "-j", "DROP"])
        return enabled

    def is_enabled(self) -> bool:
        return all(
            self._run([firewall, "-C", CHAIN, "-j", "DROP"]) != 0
            for firewall in FIREWALLS
        )

    def _ensure_chain(self, firewall: str) -> None:
        self._run([firewall, "-N", CHAIN])
        jump = [
            firewall,
            "-C",
            "DOCKER-USER",
            "-p",
            "tcp",
            "-m",
            "multiport",
            "--dports",
            PORTS,
            "-j",
            CHAIN,
        ]
        if self._run(jump) != 0:
            jump[1] = "-A"
            self._run(jump)


def _run_command(args: list[str]) -> int:
    return subprocess.run(
        args,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def handle_request(firewall: VncFirewall, request: object) -> str:
    if not isinstance(request, dict):
        return json.dumps({"error": "invalid request"})

    action = request.get("action")
    if action == "status":
        return json.dumps({"enabled": firewall.is_enabled()})
    if action == "open":
        return json.dumps({"enabled": firewall.set_enabled(True)})
    if action == "close":
        return json.dumps({"enabled": firewall.set_enabled(False)})
    return json.dumps({"error": "invalid action"})


def serve(socket_path: str = SOCKET_PATH) -> None:
    firewall = VncFirewall()
    firewall.set_enabled(False)
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(socket_path)
        os.chmod(socket_path, 0o660)
        server.listen()
        while True:
            with server.accept()[0] as connection:
                request = _read_request(connection)
                response = handle_request(firewall, request)
                connection.sendall(response.encode() + b"\n")


def _read_request(connection: socket.socket) -> object:
    line = connection.makefile("rb").readline()
    try:
        return json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


if __name__ == "__main__":
    serve()
