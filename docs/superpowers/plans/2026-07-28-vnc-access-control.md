# VNC Access Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close VNC/noVNC to the internet by default and allow an authenticated admin to open or close only ports 5900 and 6080.

**Architecture:** A root-owned Unix-socket controller manages only a dedicated Docker firewall chain. The admin app uses a small socket client and exposes a login-protected status/action control; it never gains Docker or firewall capabilities.

**Tech Stack:** Python 3 stdlib sockets/subprocess, iptables/ip6tables, systemd, FastAPI/Jinja, Docker Compose, pytest.

## Global Constraints

- The default and boot-time state is closed for TCP ports 5900 and 6080 in IPv4 and IPv6.
- Do not change SSH (22), PostgreSQL (5432), Docker volumes, or database data.
- Only the root host service may invoke iptables/ip6tables; the admin container receives a Unix socket only.
- The controller accepts exactly `status`, `open`, and `close` commands and touches only `DZEN_VNC` plus its jump in `DOCKER-USER`.
- Socket errors leave VNC closed/unchanged and are shown in the admin UI.
- Commit implementation as `fix: add admin VNC access control`.

---

## File structure

- `dzen_commenter/admin/vnc_access.py` — Unix-socket client and restricted controller protocol.
- `dzen_commenter/admin/config.py` — configurable socket path.
- `dzen_commenter/admin/app.py` — authenticated status and toggle endpoint.
- `dzen_commenter/admin/templates/settings.html` — VNC state and action button.
- `dzen_commenter/vnc_control.py` — stdlib root-controller logic, isolated command runner, and Unix server.
- `deploy/dzen-vnc-control.service` — systemd unit.
- `deploy/install-vnc-control.sh` — root-only installer of the controller and service.
- `docker-compose.yml` — bind-mount the already-created control socket into `admin`.
- `tests/admin/test_vnc_access.py`, `tests/admin/test_settings.py`, `tests/test_vnc_control.py` — client, admin, and firewall-command tests.

### Task 1: Implement and test the restricted host controller

**Files:**
- Create: `dzen_commenter/vnc_control.py`
- Create: `tests/test_vnc_control.py`
- Create: `deploy/dzen-vnc-control.service`
- Create: `deploy/install-vnc-control.sh`

**Interfaces:**
- Produces `VncFirewall.set_enabled(enabled: bool) -> bool` and `VncFirewall.is_enabled() -> bool`.
- Produces `serve(socket_path: str = "/run/dzen-vnc-control.sock") -> None` with newline-delimited JSON requests.

- [x] **Step 1: Write failing controller tests**

Use a fake command runner recording argument lists. Assert a closed controller creates/links `DZEN_VNC`, flushes it, and appends `DROP`; an open controller removes only that drop by flushing the dedicated chain. Assert every command uses either `iptables` or `ip6tables`, `DOCKER-USER`, `DZEN_VNC`, and ports `5900,6080` only.

```python
def test_close_vnc_installs_dedicated_drop_rule_for_ipv4_and_ipv6():
    runner = FakeRunner()
    firewall = VncFirewall(runner)

    firewall.set_enabled(False)

    assert ["iptables", "-A", "DZEN_VNC", "-j", "DROP"] in runner.calls
    assert ["ip6tables", "-A", "DZEN_VNC", "-j", "DROP"] in runner.calls
```

- [x] **Step 2: Run the controller tests and confirm RED**

Run: `python -m pytest tests/test_vnc_control.py -v`

Expected: FAIL because `dzen_commenter.vnc_control` does not exist.

- [x] **Step 3: Implement the minimal controller and installer**

`VncFirewall` must ensure a `DZEN_VNC` chain, ensure one `DOCKER-USER` jump matching TCP multiports `5900,6080`, flush the dedicated chain, and append `DROP` only when disabled. `is_enabled` is true only if neither IPv4 nor IPv6 chain has the drop rule. The Unix server handles one JSON line with `{"action":"status"}`, `{"action":"open"}`, or `{"action":"close"}` and returns `{"enabled": bool}`; invalid messages return an error without running firewall commands.

The systemd service runs `python3 /usr/local/lib/dzen-commenter/vnc_control.py`; its installer copies the module, installs the unit, runs `daemon-reload`, enables and restarts it. Starting the server calls `set_enabled(False)` before accepting clients. Socket mode is `0660` and the socket is not exposed over TCP.

- [x] **Step 4: Run controller tests and verify GREEN**

Run: `python -m pytest tests/test_vnc_control.py -v`

Expected: PASS.

### Task 2: Add the admin socket client and protected toggle UI

**Files:**
- Create: `dzen_commenter/admin/vnc_access.py`
- Modify: `dzen_commenter/admin/config.py`
- Modify: `dzen_commenter/admin/app.py`
- Modify: `dzen_commenter/admin/templates/settings.html`
- Modify: `tests/admin/test_vnc_access.py`
- Modify: `tests/admin/test_settings.py`

**Interfaces:**
- Consumes the controller JSON protocol from Task 1.
- Produces `VncAccessClient.status() -> bool` and `VncAccessClient.set_enabled(enabled: bool) -> bool`.
- Produces authenticated `POST /settings/vnc-access` accepting `action=open|close`.

- [x] **Step 1: Write failing client and route tests**

Inject a fake socket into `VncAccessClient` and assert it sends only a JSON action line and reads `enabled`. Make `create_app(..., vnc_access=fake)` accept a fake with `status()` and `set_enabled()`. Add:

```python
def test_authenticated_admin_can_open_vnc(client, fake_vnc):
    response = client.post("/settings/vnc-access", data={"action": "open"})
    assert response.status_code == 302
    assert fake_vnc.set_calls == [True]


def test_guest_cannot_toggle_vnc(settings):
    response = TestClient(create_app(settings), follow_redirects=False).post(
        "/settings/vnc-access", data={"action": "open"}
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/login"
```

Also assert settings renders «VNC закрыт» plus «Открыть VNC» when fake status is false, and «VNC открыт» plus «Закрыть VNC» when true.

- [x] **Step 2: Run focused admin tests and confirm RED**

Run: `python -m pytest tests/admin/test_vnc_access.py tests/admin/test_settings.py -k "vnc" -v`

Expected: FAIL because neither client nor endpoint exists.

- [x] **Step 3: Implement minimal authenticated UI**

Add `VNC_CONTROL_SOCKET: str = "/run/dzen-vnc-control.sock"` to `AdminSettings`. `VncAccessClient` uses `AF_UNIX`, a 2-second timeout, one JSON request and one response; it raises `VncAccessUnavailable` on connection, timeout, malformed response, or controller error.

Set `app.state.vnc_access` to injected client or `VncAccessClient(settings.VNC_CONTROL_SOCKET)`. The settings GET obtains state and records an unavailable message on `VncAccessUnavailable`. The POST has `Depends(require_login)`, rejects actions other than `open`/`close` with HTTP 400, calls the client, and redirects to `/settings?vnc=opened|closed`; on unavailable it redirects to `/settings?vnc=unavailable`.

Render a separate VNC access fieldset below the existing read-only VNC connection fields. Never render or accept the VNC password through the new control. Do not add JavaScript or an auto-open timer.

- [x] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/admin/test_vnc_access.py tests/admin/test_settings.py -k "vnc" -v`

Expected: PASS.

### Task 3: Wire the socket into Compose and verify the full repository

**Files:**
- Modify: `docker-compose.yml`
- Test: full suite

**Interfaces:**
- Consumes `/run/dzen-vnc-control.sock` created by the host systemd service.
- Produces an admin container with no firewall capability and access only to that socket.

- [x] **Step 1: Write a failing structural test**

Add a test reading `docker-compose.yml` and asserting the `admin` service contains the exact bind mount `/run/dzen-vnc-control.sock:/run/dzen-vnc-control.sock` and does not contain `privileged`, `NET_ADMIN`, or `/var/run/docker.sock`.

- [x] **Step 2: Run the structural test and confirm RED**

Run: `python -m pytest tests/admin/test_vnc_access.py -k "compose" -v`

Expected: FAIL because the socket mount is absent.

- [x] **Step 3: Add only the socket bind mount**

Under `services.admin.volumes`, add:

```yaml
- /run/dzen-vnc-control.sock:/run/dzen-vnc-control.sock
```

Do not add capabilities, privileged mode, Docker socket, firewall tools, or a database migration.

- [x] **Step 4: Run full verification and commit**

Run: `python -m pytest -q; git diff --check`

Expected: all tests pass and no whitespace errors.

Commit:

```bash
git add dzen_commenter deploy docker-compose.yml tests
git commit -m "fix: add admin VNC access control"
```

## Plan self-review

- Task 1 implements a closed-by-default, two-port-only host controller.
- Task 2 restricts all UI actions to authenticated admin sessions and handles unavailable control safely.
- Task 3 mounts only the Unix socket and explicitly prevents privileged-container shortcuts.
