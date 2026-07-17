# Email Fallback Live Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the current environment configuration and prove the production SMTP fallback handles a failed Telegram error notification.

**Architecture:** The server's existing `.env` is preserved as a timestamped backup before receiving the local configuration. A disposable Compose container uses an invalid Telegram token only in its process environment and calls the same `TelegramNotifier.notify_error` path as the application.

**Tech Stack:** PowerShell OpenSSH/SCP, Docker Compose, Python application modules, SMTP.

## Global Constraints

- Do not display secrets from either `.env` file.
- Keep the production `app` container running during the fault injection.
- The invalid Telegram token must exist only in the disposable container.
- Leave a timestamped server-side `.env` backup.

---

### Task 1: Deploy and verify environment configuration

**Files:**
- Read: `.env`
- Modify: server deployment `.env`

**Interfaces:**
- Consumes: `DEPLOY_HOST`, `DEPLOY_USER`, and `DEPLOY_PASSWORD` from local `.env`.
- Produces: server `.env` matching the local file and a remote backup path.

- [ ] **Step 1: Verify the server Compose project path and service state**

Run an SSH command that searches only expected deployment directories for `docker-compose.yml` and prints the `app` service status. Expected: exactly one candidate project directory and an existing `app` service.

- [ ] **Step 2: Back up the server environment file**

Run remotely from the discovered Compose project directory:

```sh
cp .env ".env.before-fallback-check-$(date +%Y%m%d%H%M%S)"
```

Expected: one timestamped backup exists beside `.env`.

- [ ] **Step 3: Upload the local environment file atomically**

Copy the local `.env` to a remote temporary name, then move it into place:

```sh
mv .env.upload .env
```

Expected: a byte-for-byte comparison against the uploaded file succeeds without printing file contents.

- [ ] **Step 4: Recreate only the application container**

Run:

```sh
docker compose up -d --no-deps --force-recreate app
docker compose ps app
```

Expected: `app` reports `running`.

### Task 2: Run the isolated fallback test

**Files:**
- Read: `dzen_commenter/monitoring/telegram_notifier.py`
- Read: `dzen_commenter/monitoring/email_fallback.py`

**Interfaces:**
- Consumes: `TelegramNotifier.notify_error(message: str, error: Exception | None)`.
- Produces: one SMTP email whose body includes the test marker and `RuntimeError` details.

- [ ] **Step 1: Launch a disposable container with a fake token**

Run from the Compose project directory:

```sh
docker compose run --rm -e TELEGRAM_BOT_TOKEN=invalid-live-fallback-test app python -c "from dzen_commenter.monitoring.email_fallback import EmailFallbackNotifier; from dzen_commenter.monitoring.telegram_notifier import TelegramNotifier; import os; e=EmailFallbackNotifier(host=os.environ['SMTP_HOST'],port=int(os.environ['SMTP_PORT']),user=os.environ['SMTP_USER'],password=os.environ['SMTP_PASSWORD'],from_addr=os.environ['SMTP_FROM'],to_addrs=[a.strip() for a in os.environ['EMAIL_FALLBACK_LIST'].split(',') if a.strip()]); TelegramNotifier(bot_token=os.environ['TELEGRAM_BOT_TOKEN'],chat_id=os.environ['DEVELOPER_TELEGRAM_CHAT_ID_LIST'],proxy_url=os.environ.get('TELEGRAM_PROXY_URL',''),fallback=e).notify_error('LIVE_FALLBACK_TEST_20260717', RuntimeError('intentional notification test'))"
```

Expected: command exits `0`; Telegram delivery fails internally and the SMTP fallback sends the test email.

- [ ] **Step 2: Confirm delivery**

Check the configured fallback mailbox for subject `Dzen Commenter notification` and body marker `LIVE_FALLBACK_TEST_20260717`. Expected: one newly received message containing `RuntimeError: intentional notification test`.

- [ ] **Step 3: Confirm the production service stayed healthy**

Run:

```sh
docker compose ps app
docker compose logs --tail=50 app
```

Expected: `app` remains running and logs show no restart or configuration failure caused by the disposable check.
