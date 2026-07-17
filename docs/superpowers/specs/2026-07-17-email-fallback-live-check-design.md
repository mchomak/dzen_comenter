# Live email fallback check

## Goal

Verify in the deployed environment that a failed developer Telegram notification is delivered through the configured SMTP fallback.

## Procedure

1. Back up the server `.env` and upload the current local version.
2. Start a disposable application container with an invalid Telegram bot token, preserving all other server configuration.
3. Invoke `notify_error` with a named, artificial exception inside that container.
4. Confirm delivery of the fallback email and retain the server's valid `.env` for the running service.

## Safety

The production application container is not modified or restarted for the notification test. The invalid token exists only in the disposable test container. The server `.env` is backed up before replacement.

## Success criteria

- The server `.env` equals the intended current local configuration.
- The test invocation cannot send Telegram successfully.
- An email with the test marker and exception details reaches the configured fallback recipient.
- The production service remains running after the check.
