#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
VNC_PORT="${VNC_PORT:-5900}"
VNC_PASSWORD="${VNC_PASSWORD:-}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
NOVNC_WEB_ROOT="${NOVNC_WEB_ROOT:-/usr/share/novnc}"

cleanup() {
  local status=$?
  kill "${app_pid:-}" "${websockify_pid:-}" "${x11vnc_pid:-}" "${xvfb_pid:-}" 2>/dev/null || true
  wait "${app_pid:-}" "${websockify_pid:-}" "${x11vnc_pid:-}" "${xvfb_pid:-}" 2>/dev/null || true
  rm -f "/tmp/.X${DISPLAY#:}-lock"
  exit "$status"
}

trap cleanup EXIT
trap 'exit 0' INT TERM

rm -f "/tmp/.X${DISPLAY#:}-lock"
Xvfb "$DISPLAY" -screen 0 1280x800x24 &
xvfb_pid=$!

for _ in $(seq 1 50); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

if [ -n "$VNC_PASSWORD" ]; then
  x11vnc -display "$DISPLAY" -forever -rfbport "$VNC_PORT" -passwd "$VNC_PASSWORD" &
else
  x11vnc -display "$DISPLAY" -forever -rfbport "$VNC_PORT" -nopw &
fi
x11vnc_pid=$!

websockify --web="$NOVNC_WEB_ROOT" "$NOVNC_PORT" "127.0.0.1:$VNC_PORT" &
websockify_pid=$!

alembic upgrade head

"$@" &
app_pid=$!
set +e
wait "$app_pid"
exit $?
