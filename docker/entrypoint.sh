#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
VNC_PORT="${VNC_PORT:-5900}"
VNC_PASSWORD="${VNC_PASSWORD:-}"

Xvfb "$DISPLAY" -screen 0 1280x800x24 &

if [ -n "$VNC_PASSWORD" ]; then
  x11vnc -display "$DISPLAY" -forever -rfbport "$VNC_PORT" -passwd "$VNC_PASSWORD" &
else
  x11vnc -display "$DISPLAY" -forever -rfbport "$VNC_PORT" -nopw &
fi

exec "$@"
