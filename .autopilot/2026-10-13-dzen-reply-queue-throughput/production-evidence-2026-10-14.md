# Production evidence — 2026-10-14

All evidence below is from read-only checks. No comment text, author names, URLs, HTML, credentials, or row identifiers are included. Times are UTC.

`comments.fetched_at`, `replies.created_at`, and `replies.published_at` are PostgreSQL `timestamp without time zone` columns. The application writes these as naive Moscow wall-clock values via `moscow_now()`. Convert UTC release/snapshot cutoffs to Europe/Moscow before filtering those columns; latency differences between fetched and published timestamps use the same local representation.

## Deployment and preservation

Snapshot: `2026-10-14T12:07:41Z`.

```text
app: dzen_commenter-app:4cd5534, Up 23 minutes (healthy)
admin: dzen_commenter-app:4cd5534, Up 23 minutes
postgres: Up 9 days (healthy)
app source commit: 4cd553439874c51b4da13a1c48738bf9346b6f54
admin /login: HTTP 200
bot health: in_progress; heartbeat 2026-10-14T12:00:24.788080+00:00
schema: 0012_ai_call_reservations (head)
active app worker containers: 1
rollback image: dzen_commenter-app:832a4cb (present)
postgres container ID: 82bfa98ec907e2fbb30614b309757d6e936c5768f1fa77b78524f3a952492e17
postgres volume: dzen_commenter_pgdata -> /var/lib/postgresql/data
app volumes: dzen_commenter_session_state, dzen_commenter_config_data,
             dzen_commenter_dzen_login_control, dzen_commenter_browser_data
```

The pre-redeploy backup is `/root/backups/dzen_commenter-20261014-ticket09-before-redeploy.dump`.

```text
bytes=3151546 mode=600
Archive created at 2026-10-14 11:40:19 UTC
dbname: dzen_commenter
TOC Entries: 70
Compression: gzip
pg_restore --list total lines: 81
```

The backup was listed by `docker exec -i dzen_commenter-postgres-1 pg_restore --list < /root/backups/dzen_commenter-20261014-ticket09-before-redeploy.dump`; it was not restored or modified.

### Rollout order and preserved volumes

Read-only Docker/image metadata and a sanitized extraction of the deployment shell history returned:

```text
history line 694: stop-old-app
history lines 705–708: backup operation
history line 728: build-release-image
history line 731: compose-up
backup file modified: 2026-10-14T11:40:20.668Z
new image created: 2026-10-14T11:41:29.123Z
new app container created: 2026-10-14T11:41:53.818Z
new app container started: 2026-10-14T11:41:56.228Z
first application work event / metric cutoff: 2026-10-14T11:42:04Z
```

The shell history has no per-command timestamps, so the exact second of the old-worker stop is unavailable. It records the stop command before the backup and build commands; the backup and image metadata timestamp the later steps. Docker's retained event stream and journal returned no lifecycle record for the removed old container. The recorded order therefore establishes stop-before-build, but not a precise stop timestamp. The redeploy applied no migration: the running image reports `0012_ai_call_reservations (head)` and Ticket 09 adds no migration.

The pre-redeploy check recorded PostgreSQL container ID `82bfa98ec907e2fbb30614b309757d6e936c5768f1fa77b78524f3a952492e17`; the post-redeploy inspection returned the same ID. Its Docker `Created` time is `2026-10-04T13:22:55.772650804Z`, before this release. The same inspection returned volume `dzen_commenter_pgdata` mounted at `/var/lib/postgresql/data`. The four app volume names in the deployment snapshot were also created before the release: session/config/browser volumes on `2026-07-22T13:30:36Z` and login-control on `2026-08-21T15:30:48Z`. Their age and current mounts show that the redeploy reused the existing volumes rather than creating replacement volumes.

## Queue snapshot

The SQL snapshot at `2026-10-14T12:07:41Z` returned:

```text
generation_states=completed:4049
publication_states=claimed:2,completed:3307,queued:28,waiting:21
pending_generated_replies=51
generated_without_publication_job=0
comments_fetched_since_release=0 (corrected with Moscow-local cutoff)
new_comment_statuses=none
new_comment_generation_queue=none
new_comment_publication_queue=none
published_replies_total=2078
```

For the `MAX_ACTIVE_WORK_ITEMS=30` criterion, `queued` and `claimed` occupy slots; `waiting` is explicitly outside the active window. Therefore the active publication set was `28 + 2 = 30`; 21 additional publication jobs were waiting outside the window. The count of 51 combined active and waiting states and was not the active-window count. No generated reply was missing its publication job.

**Correction to the first report:** its UTC cutoff was compared directly with Moscow-local naive database timestamps, shifting the selected period. The 16 comments previously called “new since release” were fetched before the release; their present statuses are not evidence of release admission. With the correct Moscow-local window (`14:42:04`–`15:07:41`), zero comments were fetched between the first worker event and this snapshot. The `new_comment_statuses` and `new_comment_*_queue` values from that first report are withdrawn.

Read-only aggregate queries used these definitions:

```sql
SELECT state, count(*) FROM reply_publication_queue GROUP BY state;
SELECT count(*) FROM replies r
LEFT JOIN reply_publication_queue q ON q.reply_id = r.id
WHERE r.status = 'generated' AND q.state IS DISTINCT FROM 'completed';
SELECT count(*) FROM replies r
LEFT JOIN reply_publication_queue q ON q.reply_id = r.id
WHERE r.status = 'generated' AND q.reply_id IS NULL;
SELECT count(*) FROM comments
WHERE fetched_at >= TIMESTAMP '2026-10-14 14:42:04'
  AND fetched_at < TIMESTAMP '2026-10-14 15:07:41';
```

## Publication and stale-work metrics

From release time `2026-10-14T11:42:04Z` through the snapshot, **13** replies were confirmed published; all 13 were for comments present before release. The UTC-filtered count of 21 in the first report was incorrect. For these 13 replies with a non-null `comments.fetched_at`, `published_at - fetched_at` averaged **17,807 seconds (4 h 56 min 47 sec)** and had a median of **18,043 seconds (5 h 00 min 43 sec)**. Observation duration was **1,537 seconds (25 min 37 sec)**; both duration (<24 h) and sample (<30) made this preliminary.

At the first reconciliation in this run, skipped replies with explicit reasons rose from `2,354` to `2,855`: `2,855 - 2,354 = 501` newly completed stale records, all with reasons. Current totals are `1,511` skipped replies with a reason and `1,344` historical skipped replies without a reason. The 501 delta occurred during the first rollout, before the Ticket 09 redeploy.

Earlier sanitized telemetry for `11:42–11:59Z` contained 31 source-search events, a maximum of 100 post groups scanned, median duration `1,719.13 ms`, maximum `3,813.81 ms`, 13 retryable `source_comment_not_found` results, and 11 confirmed/completed publications by that earlier snapshot. No deep-search fallback event was observed.

No cache cleanup, swap change, volume removal, PostgreSQL recreation, or database restore occurred during this redeploy.

## Ticket 10 short-scan redeploy

Snapshot: `2026-10-14T13:32:44Z`.

```text
app: dzen_commenter-app:44c29b8, Up 8 minutes (healthy)
admin: dzen_commenter-app:44c29b8, HTTP /login=200
postgres: healthy; same container ID 82bfa98ec907e2fbb30614b309757d6e936c5768f1fa77b78524f3a952492e17
schema: 0012_ai_call_reservations (head)
active app worker containers: 1
worker heartbeat: 2026-10-14T13:32:10.203725+00:00; authenticated=true; cycle_in_progress=false; cycle_succeeded=true
error/critical log lines in the last 10 minutes: 0
app volumes: dzen_commenter_browser_data, dzen_commenter_session_state,
             dzen_commenter_config_data, dzen_commenter_dzen_login_control
postgres volume: dzen_commenter_pgdata -> /var/lib/postgresql/data
```

Before this redeploy, the idle worker was stopped, then a PostgreSQL archive was created and verified with `pg_restore --list`. Backup: `/root/backups/dzen_commenter-20261014-ticket10-before-redeploy.dump`; 3,154,151 bytes, mode `600`, SHA-256 `b07a27a5d9e0a3222479a8ccf360e3edbbe9e31a4fe987cd40997e004135723d`. Existing PostgreSQL container and named volumes were preserved. No migration was added by this ticket.

The read-only database snapshot at `13:32:44Z` returned:

```text
generation_states=completed:4060
publication_states=completed:3341,queued:26
generated_replies_waiting_for_publication=26
generated_replies_missing_publication_job=0
comments_fetched_since_queue_rollout=11
stale_completions_with_age_reason=501
```

There were 26 active publication jobs of the 30-item window; no other active or waiting queue state was present in this snapshot. **31** replies had been confirmed published since the first queue rollout at `11:42:04Z`; `published_at - fetched_at` averaged **13,191 seconds (3 h 39 min 51 sec)** and had a median of **14,055 seconds (3 h 54 min 15 sec)**. The sample reached 30, but the observation lasted only **6,640 seconds (1 h 50 min 40 sec)**, short of the 24-hour evaluation window, so the capacity result remains preliminary. Since the Ticket 10 image started at `13:24:13Z`, 4 replies were confirmed published by this snapshot (8 min 31 sec). These are early observations, not a forecast of sustained daily throughput.
