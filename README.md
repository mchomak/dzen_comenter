# dzen-commenter

AI community manager for Yandex Dzen comments. Stack: Python 3.11 + Playwright + PostgreSQL + Docker. Architecture is fully synchronous (single poller loop, single channel).

This is the **Wave 0 foundation**: frozen package tree, domain models, enum statuses, `Protocol` interfaces, `Settings`, all dependencies, and a docker skeleton. No business logic yet.

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\pip install -r requirements.txt
# Linux/macOS
.venv/bin/pip install -r requirements.txt

cp .env.example .env   # then fill in values
```

## Run tests

```bash
# Windows
.venv\Scripts\python -m pytest -q
# Linux/macOS
.venv/bin/python -m pytest -q
```

## Remote Access (noVNC)

The Docker container starts a virtual display on `DISPLAY=:99`, an x11vnc
server, and a noVNC web gateway. Set `VNC_PASSWORD` and `NOVNC_PORT` in `.env`,
then open `http://<server-ip>:${NOVNC_PORT}/vnc.html` in a browser. Enter the
same `VNC_PASSWORD` in the noVNC connection dialog. Keep `AUTO_PUBLISH=false`
while testing authentication and browser behavior.

## Admin panel

Start the bot, database, and panel with `docker compose up -d postgres app admin`.
The panel is available at `http://<server-ip>:8080`; set `ADMIN_PASSWORD` in
`.env` before starting it. The bot and panel share the runtime configuration
through the `config_data` Docker volume.
