# VNC access control design

## Goal

Keep the Dzen VNC and noVNC ports closed to the internet by default, and let
an authenticated administrator open or close only those ports from the admin
settings page.

## Design

A root-owned host process listens only on `/run/dzen-vnc-control.sock`. It
accepts three local commands: `status`, `open`, and `close`. The controller
manages a dedicated `DZEN_VNC` chain in Docker's `DOCKER-USER` firewall chain
for TCP ports 5900 and 6080, in both IPv4 and IPv6. Its startup action closes
the ports, so a reboot never restores public VNC access accidentally.

The admin container mounts that Unix socket. Its normal login-protected
settings page calls a small client that can only send those three commands;
the admin application receives no firewall or Docker privileges. The page
shows the current state and one action button: open when closed, close when
open. A socket failure is shown as unavailable and does not change the state.

The controller is installed and started by a root-only deployment script and a
systemd service. The deployment creates no database migration and does not
remove Docker volumes, containers, or database data.

## Verification

- Unit tests cover the Unix-socket request client and authenticated admin
  endpoints with a fake controller.
- Controller tests cover closed-by-default firewall commands without applying
  them to the test host.
- Deployment verifies SSH remains reachable while VNC/noVNC are blocked,
  app/admin health checks pass, and the controller reports closed by default.
