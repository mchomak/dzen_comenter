#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 1
fi

install -Dm644 dzen_commenter/vnc_control.py /usr/local/lib/dzen-commenter/vnc_control.py
install -Dm644 deploy/dzen-vnc-control.service /etc/systemd/system/dzen-vnc-control.service
systemctl daemon-reload
systemctl enable --now dzen-vnc-control.service
