# Developer Error Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route bot `ERROR`/`CRITICAL` failures to the developer's Telegram chat and use the configured email list when Telegram or its proxy is unavailable.

**Architecture:** Keep Telegram auth and developer alert destinations separate. Add a logging handler that forwards only error-level records to a guarded notifier; the existing Telegram notifier remains the primary transport and its email fallback remains the secondary transport. Delivery failures are contained and locally logged without recursive alerts.

**Tech Stack:** Python, `logging`, `httpx`, `smtplib`, pytest, pydantic-settings.

## Global Constraints

- `TELEGRAM_CHAT_ID` remains the auth-command destination.
- `DEVELOPER_TELEGRAM_CHAT_ID` is the alert destination.
- Only `ERROR` and `CRITICAL` records are forwarded.
- Delivery failures must not crash the supervised loop or recurse into notifications.

---

### Task 1: Add failing tests for developer routing

**Files:**
- Modify: `tests/config/test_config_extension.py`
- Modify: `tests/monitoring/test_telegram_notifier.py`
- Create: `tests/monitoring/test_developer_notifier.py`
- Modify: `tests/main.py` if needed for wiring coverage

**Interfaces:** Tests define the expected `DeveloperNotifier` and logging handler behavior before implementation.

- [ ] Add tests that require `DEVELOPER_TELEGRAM_CHAT_ID` and preserve `TELEGRAM_CHAT_ID` for auth.
- [ ] Add tests for forwarding `ERROR`/`CRITICAL`, ignoring `WARNING`, and containing SMTP failures.
- [ ] Run focused tests and confirm they fail for missing behavior.

### Task 2: Implement guarded developer notification routing

**Files:**
- Create: `dzen_commenter/monitoring/developer_notifier.py`
- Modify: `dzen_commenter/monitoring/telegram_notifier.py`
- Modify: `dzen_commenter/monitoring/logging_config.py`
- Modify: `dzen_commenter/contracts/interfaces.py` only if a narrow protocol is required

**Interfaces:** `DeveloperNotifier.notify`, `DeveloperNotifier.notify_error`, and an error-level logging handler.

- [ ] Implement the smallest notifier wrapper that uses the developer Telegram ID and existing email fallback.
- [ ] Make transport failures safe and avoid emitting alerts from inside the alert handler.
- [ ] Run focused tests until green.

### Task 3: Wire configuration and runtime usage

**Files:**
- Modify: `dzen_commenter/config/settings.py`
- Modify: `main.py`
- Modify: `.env.example`
- Modify: `tests/test_main.py`

**Interfaces:** `build_app` constructs the developer notifier with `DEVELOPER_TELEGRAM_CHAT_ID`; auth keeps `TELEGRAM_CHAT_ID`.

- [ ] Add the new environment setting and example entry.
- [ ] Wire the logging bridge after the developer notifier is created.
- [ ] Ensure existing supervised-loop notifications use the developer notifier.
- [ ] Run wiring tests and the complete suite.

### Task 4: Verify and commit

- [ ] Run the complete pytest command and inspect the full result.
- [ ] Review the diff for scope and secrets.
- [ ] Commit the implementation with a focused message.
