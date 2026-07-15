# Browser Recovery and Swap Design

## Goal

Prevent Chromium renderer crashes caused by memory pressure from leaving the bot in a permanently failed keep-alive state, without requiring a RAM upgrade.

## Design

`PlaywrightSessionManager.keep_alive()` continues to use a normal page reload when the page is healthy. If Playwright reports that the page or browser has crashed, the manager closes the damaged Playwright objects, starts a fresh persistent context using the existing `USER_DATA_DIR`, selects/creates the page, and opens `COMMENTS_URL`. The original exception is re-raised if recovery fails so the existing Telegram notification remains available.

The recovery path does not clear cookies or storage state. Authentication is therefore preserved by the persistent browser profile; normal login/restore behavior remains unchanged.

Regression tests cover both the healthy reload path and recovery after a crashed page.

The server receives a swap file on the existing disk, enabled persistently through `/etc/fstab`. The deployment is rebuilt and restarted after the code change. No paid RAM upgrade or browser-volume deletion is required.

## Acceptance Criteria

- A healthy `keep_alive()` invokes `page.reload()` once and does not restart the context.
- A `Page crashed` error causes a new persistent context to be started and `COMMENTS_URL` to be opened.
- If recovery fails, the original keep-alive call still raises an exception for the supervisor notifier.
- The server reports an active swap file via `swapon --show` after configuration.
- The rebuilt app container starts successfully and remains running after deployment.
