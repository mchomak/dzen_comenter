# Browser Recovery and Swap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Recover the Playwright browser session after a crash and add persistent swap on the existing server disk without increasing RAM.

**Architecture:** Keep the healthy reload path unchanged. On a crashed page, rebuild the persistent Playwright context with the existing browser profile and navigate to the configured comments URL. Configure swap independently on the host, then rebuild the existing Docker Compose app.

**Tech Stack:** Python 3.11, Playwright sync API, pytest, Docker Compose, Ubuntu swap tools.

## Global Constraints

- Do not clear browser data or storage state during recovery.
- Do not modify unrelated untracked `.agents/` or `.codex/` files.
- Preserve the supervisor's Telegram notification when recovery fails.
- Do not expose credentials in command output or commit history.

### Task 1: Add crash-recovery regression tests

**Files:**
- Modify: `tests/browser/test_session_manager.py`

- [ ] Write a test proving a normal keep-alive only reloads the current page.
- [ ] Write a test proving a `Page crashed` reload error restarts the persistent context and navigates to `COMMENTS_URL`.
- [ ] Run the focused tests and confirm the new crash-recovery test fails for the current implementation.

### Task 2: Implement minimal browser recovery

**Files:**
- Modify: `dzen_commenter/browser/session_manager.py`

- [ ] Add a private restart helper that closes the current context/Playwright driver, starts a fresh persistent context with the same settings, selects/creates a page, and navigates to `COMMENTS_URL`.
- [ ] Catch only Playwright errors whose message indicates a crashed/closed page or browser, invoke the restart helper, and re-raise if restarting fails.
- [ ] Run focused tests, then the complete pytest suite.

### Task 3: Commit and deploy

**Files:**
- No additional repository files.

- [ ] Review the diff and commit the code/test changes with a stage-specific message.
- [ ] Push the current branch to its configured remote.
- [ ] On the server, verify disk space, create a nonzero swap file only if absent, add an idempotent `/etc/fstab` entry, and activate it.
- [ ] Rebuild and restart the Docker Compose app.
- [ ] Verify `swapon --show`, container health/status, and recent application logs.
