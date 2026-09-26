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

## Browser and configuration

Docker Compose runs Chromium headless by default. In this mode the entrypoint
does not start Xvfb, x11vnc, or noVNC, and the Compose file publishes neither
browser ports nor PostgreSQL's port 5432. The admin panel remains available on
port 8080. Set `HEADLESS=false` only when a display is needed; changing it
requires recreating the app container.

The admin panel stores live, non-secret controls and prompt text in the shared
runtime JSON; the bot reloads these values without a restart. Tokens, the
Telegram proxy (`TELEGRAM_PROXY_URL`), and Compose PostgreSQL credentials
(`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`) belong in `.env` and are
read at container startup. The panel cannot view or change those secrets.

Compose builds the internal database URL using the `postgres` service name.
PostgreSQL is reachable from the Compose network, not directly from the public
host. Changing PostgreSQL environment values does not rotate credentials in an
already initialized data volume.

## Admin panel

Start the bot, database, and panel with `docker compose up -d postgres app admin`.
The panel is available at `http://<server-ip>:8080`; set `ADMIN_PASSWORD` in
`.env` before starting it. The bot and panel share the runtime configuration
through the `config_data` Docker volume.
