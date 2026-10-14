# Production evidence — 2026-10-14

All evidence below is from read-only checks. No comment text, author names, URLs, HTML, credentials, or row identifiers are included. Times are UTC.

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

## Queue snapshot

The SQL snapshot at `2026-10-14T12:07:41Z` returned:

```text
generation_states=completed:4049
publication_states=claimed:2,completed:3307,queued:28,waiting:21
pending_generated_replies=51
generated_without_publication_job=0
comments_fetched_since_release=16
new_comment_statuses=generated:15,skipped:1
new_comment_generation_queue=completed:16
new_comment_publication_queue=waiting:15
published_replies_total=2078
```

For the `MAX_ACTIVE_WORK_ITEMS=30` criterion, `queued` and `claimed` occupy slots; `waiting` is explicitly outside the active window. Therefore the current active publication set is `28 + 2 = 30`; 21 additional publication jobs are waiting outside the window. The earlier count of 51 combined active and waiting states and was not the active-window count. No generated reply is missing its publication job. Sixteen newly fetched comments are stored; fifteen generated replies have persisted publication jobs waiting for slots while the older active publication work drains, and one was skipped.

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
WHERE fetched_at >= TIMESTAMPTZ '2026-10-14 11:42:04+00';
```

## Publication and stale-work metrics

From release time `2026-10-14T11:42:04Z` through the snapshot, 21 replies were confirmed published; all 21 were for comments already present before release. No newly fetched comment had a confirmed publication yet. For the 21 confirmed publications with a non-null `comments.fetched_at`, `published_at - fetched_at` averaged `21,304` seconds (computed display: **5 h 55 min**) and had a median of `20,688` seconds (**5 h 45 min**). Observation duration was `1,537` seconds (25 min 37 sec); both the duration (<24 h) and sample (<30) make this preliminary.

At the first reconciliation in this run, skipped replies with explicit reasons rose from `2,354` to `2,855`: `2,855 - 2,354 = 501` newly completed stale records, all with reasons. Current totals are `1,511` skipped replies with a reason and `1,344` historical skipped replies without a reason. The 501 delta occurred during the first rollout, before the Ticket 09 redeploy.

Earlier sanitized telemetry for `11:42–11:59Z` contained 31 source-search events, a maximum of 100 post groups scanned, median duration `1,719.13 ms`, maximum `3,813.81 ms`, 13 retryable `source_comment_not_found` results, and 11 confirmed/completed publications by that earlier snapshot. No deep-search fallback event was observed.

No cache cleanup, swap change, volume removal, PostgreSQL recreation, or database restore occurred during this redeploy.
