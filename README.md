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
