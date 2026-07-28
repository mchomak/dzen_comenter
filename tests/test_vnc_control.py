import json

from dzen_commenter.vnc_control import VncFirewall, handle_request


class FakeRunner:
    def __init__(self, results=None):
        self.calls: list[list[str]] = []
        self.results = results or {}

    def run(self, args: list[str]) -> int:
        self.calls.append(args)
        return self.results.get(tuple(args), 1)


def test_close_vnc_installs_dedicated_drop_rule_for_ipv4_and_ipv6():
    runner = FakeRunner()
    firewall = VncFirewall(runner)

    firewall.set_enabled(False)

    assert ["iptables", "-A", "DZEN_VNC", "-j", "DROP"] in runner.calls
    assert ["ip6tables", "-A", "DZEN_VNC", "-j", "DROP"] in runner.calls
    for command in runner.calls:
        assert command[0] in {"iptables", "ip6tables"}
        assert set(command) & {"DOCKER-USER", "DZEN_VNC"}
        if "--dports" in command:
            assert command[command.index("--dports") + 1] == "5900,6080"


def test_open_vnc_flushes_only_the_dedicated_chain():
    runner = FakeRunner()
    firewall = VncFirewall(runner)

    firewall.set_enabled(True)

    assert ["iptables", "-F", "DZEN_VNC"] in runner.calls
    assert ["ip6tables", "-F", "DZEN_VNC"] in runner.calls
    assert ["iptables", "-A", "DZEN_VNC", "-j", "DROP"] not in runner.calls
    assert ["ip6tables", "-A", "DZEN_VNC", "-j", "DROP"] not in runner.calls


def test_firewall_is_open_only_when_both_protocol_chains_lack_drop_rules():
    drop = ("iptables", "-C", "DZEN_VNC", "-j", "DROP")
    runner = FakeRunner({drop: 0})

    assert VncFirewall(runner).is_enabled() is False

    runner = FakeRunner()
    assert VncFirewall(runner).is_enabled() is True


def test_controller_handles_only_status_open_and_close_without_running_commands_for_invalid_input():
    runner = FakeRunner()
    firewall = VncFirewall(runner)

    assert json.loads(handle_request(firewall, {"action": "open"})) == {"enabled": True}
    assert json.loads(handle_request(firewall, {"action": "close"})) == {"enabled": False}
    before = list(runner.calls)

    assert json.loads(handle_request(firewall, {"action": "other"})) == {"error": "invalid action"}
    assert runner.calls == before
