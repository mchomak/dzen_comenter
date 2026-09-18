# Воспроизводимые запросы исторического анализа

Все запросы запускались только на чтение в production PostgreSQL (`Etc/UTC`). Период среза для полных суток: до `2026-09-17 00:00:00+00`.

## Интервальные агрегаты

```sql
WITH intervals AS (
  SELECT 'article_clean_pre_batch'::text AS label,
         timestamptz '2026-08-24 19:16:29+00' AS started_at,
         timestamptz '2026-08-30 15:47:07.867397+00' AS ended_at
  UNION ALL SELECT 'early_batch_before_recovery',
         timestamptz '2026-08-30 15:47:07.867397+00', timestamptz '2026-09-05 09:42:00+00'
  UNION ALL SELECT 'batch_recovery_before_durable_queue',
         timestamptz '2026-09-05 09:42:00+00', timestamptz '2026-09-10 11:09:08+00'
  UNION ALL SELECT 'durable_queue_before_sanitizer',
         timestamptz '2026-09-10 11:09:08+00', timestamptz '2026-09-12 21:45:57+00'
  UNION ALL SELECT 'sanitizer_before_current_deploy',
         timestamptz '2026-09-12 21:45:57+00', timestamptz '2026-09-15 11:09:12+00'
  UNION ALL SELECT 'current_deploy_to_utc_cutoff',
         timestamptz '2026-09-15 11:09:12+00', timestamptz '2026-09-17 00:00:00+00'
)
SELECT i.label,
       round(extract(epoch FROM i.ended_at-i.started_at)/3600.0, 2) AS hours,
       (SELECT count(*) FROM comments c WHERE c.fetched_at >= i.started_at AND c.fetched_at < i.ended_at) AS comments,
       (SELECT count(*) FROM replies r WHERE r.created_at >= i.started_at AND r.created_at < i.ended_at) AS replies_created,
       (SELECT count(*) FROM replies r WHERE r.created_at >= i.started_at AND r.created_at < i.ended_at AND r.status='error') AS replies_error_current,
       (SELECT count(*) FROM replies r WHERE r.created_at >= i.started_at AND r.created_at < i.ended_at AND r.error_reason LIKE 'Batch reply generation failed:%') AS generation_errors,
       (SELECT count(*) FROM replies r WHERE r.created_at >= i.started_at AND r.created_at < i.ended_at AND r.error_reason LIKE 'Dzen reply publication failed:%') AS publication_errors,
       (SELECT count(*) FROM replies r WHERE r.created_at >= i.started_at AND r.created_at < i.ended_at AND coalesce(r.generated_text,'') <> '') AS nonempty_texts,
       (SELECT count(*) FROM replies r WHERE r.created_at >= i.started_at AND r.created_at < i.ended_at
          AND position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(r.generated_text)) > 0
          AND position(chr(1086)||chr(1090)||chr(1074)||chr(1077)||chr(1090)||chr(58) IN lower(r.generated_text)) > 0) AS type_answer_markers,
       (SELECT count(*) FROM replies r WHERE r.created_at >= i.started_at AND r.created_at < i.ended_at
          AND position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(r.generated_text)) > 0
          AND position('skip' IN lower(r.generated_text)) > 0) AS type_skip_markers,
       (SELECT count(*) FROM reply_batches b WHERE b.created_at >= i.started_at AND b.created_at < i.ended_at) AS batches,
       (SELECT coalesce(sum(b.item_count),0) FROM reply_batches b WHERE b.created_at >= i.started_at AND b.created_at < i.ended_at) AS batch_items,
       (SELECT count(*) FROM reply_batches b WHERE b.created_at >= i.started_at AND b.created_at < i.ended_at AND b.item_count=3) AS batch_3
FROM intervals i
ORDER BY i.started_at;
```

## Размер и outcome batch

```sql
SELECT b.item_count, b.status, count(*) AS batches
FROM reply_batches b
WHERE b.created_at >= timestamptz '2026-08-30 15:47:07.867397+00'
  AND b.created_at <  timestamptz '2026-09-17 00:00:00+00'
GROUP BY b.item_count, b.status
ORDER BY b.item_count, b.status;
```

## Неизменяемость token-данных

```sql
SELECT count(*) AS batches,
       count(*) FILTER (WHERE prompt_tokens IS NOT NULL) AS with_prompt_tokens,
       count(*) FILTER (WHERE completion_tokens IS NOT NULL) AS with_completion_tokens,
       coalesce(sum(prompt_tokens), 0) AS prompt_tokens_sum,
       coalesce(sum(completion_tokens), 0) AS completion_tokens_sum
FROM reply_batches;
```

## Git и доступные production-deploy evidence

```powershell
git rev-list --count --all
git log --all --date=iso-strict --pretty=format:'%H%x09%ad%x09%s' --reverse
```

```bash
git reflog --date=iso --format='%gd%x09%gs'
docker inspect -f '{{.State.StartedAt}}|{{.Created}}' dzen_commenter-app-1
docker compose logs --timestamps app
```

`git reflog` доказывает изменение рабочей копии на сервере, но не сам по себе сборку/перезапуск контейнера. Docker logs на момент исследования начинались со старта текущего app-container 2026-09-15 11:09 UTC; отсутствие более ранних строк не означает отсутствие ошибок.
