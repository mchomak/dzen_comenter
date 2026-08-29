# Приёмка

## Результат

- Fix committed and deployed: `2eb93aa fix: read claimed batch rows by mapping`.
- The original deployment first stopped at a stale Chromium profile lock. After stopping only `app`, the three explicit Chromium lock links were removed from the existing browser-data volume; app, admin and PostgreSQL then started successfully. PostgreSQL data was not removed or recreated.
- Server verification: `0008_reply_batches (head)`; app and PostgreSQL healthy; admin returned HTTP 302; fresh app/admin/postgres log scan found no `ERROR`, `CRITICAL`, traceback, `AttributeError` or `NoSuchColumnError`.
- Test suite inside the deployed image: `411 passed, 32 skipped, 1 warning`.
- A read-only real-PostgreSQL projection check confirmed the six labelled batch mapping keys used by `claim_next_batch`.

## Runtime pilot configuration

- `batch_replies_enabled=true`
- `batch_max_comments=3`
- `batch_wait_hours=12`
- `batch_retry_cooldown_minutes=60`
- `batch_max_attempts_per_comment=2`
- `auto_publish=false`

## Independent checks

- Specification coverage: no missing brief requirements.
- Ticket review: PASS after one repair; the first attempted mapping keyed ORM classes incorrectly and was not deployed.
- Blind acceptance of the source commit confirmed the bug fix and origin push. It could not independently observe the live server by design; server evidence above closes that deployment-only gap.

## Security follow-up

One overly broad diagnostic command exposed Compose environment values to the tool log. Values are not reproduced or stored in this report. Rotate the server secrets exposed through Compose configuration before treating the server as production-safe.
