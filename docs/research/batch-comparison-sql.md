# SQL-запросы для сравнения batch и одиночного режима

Все запросы ниже выполнялись только на чтение в production PostgreSQL. Временная зона сервера PostgreSQL — `Etc/UTC`. Окна: `2026-07-25 00:00:00+00`–`2026-08-01 00:00:00+00` и `2026-09-08 00:00:00+00`–`2026-09-15 00:00:00+00`.

## Итоговые статусы по комментариям и ответам

```sql
WITH windows AS (
    SELECT 'pre_july'::text AS label,
           timestamptz '2026-07-25 00:00:00+00' AS started_at,
           timestamptz '2026-08-01 00:00:00+00' AS ended_at
    UNION ALL
    SELECT 'post_sep',
           timestamptz '2026-09-08 00:00:00+00',
           timestamptz '2026-09-15 00:00:00+00'
)
SELECT w.label,
       count(DISTINCT c.id) AS comments,
       count(DISTINCT c.id) FILTER (WHERE c.status = 'skipped') AS comments_skipped,
       count(DISTINCT c.id) FILTER (WHERE c.status = 'error') AS comments_error,
       count(r.id) AS replies,
       count(r.id) FILTER (WHERE r.status = 'published') AS replies_published,
       count(r.id) FILTER (WHERE r.status = 'generated') AS replies_generated,
       count(r.id) FILTER (WHERE r.status = 'skipped') AS replies_skipped,
       count(r.id) FILTER (WHERE r.status = 'error') AS replies_error,
       count(r.id) FILTER (WHERE r.error_reason ILIKE 'Batch reply generation failed:%')
           AS batch_generation_errors,
       count(r.id) FILTER (WHERE r.error_reason ILIKE 'Dzen reply publication failed:%')
           AS publication_errors
FROM windows w
LEFT JOIN comments c
  ON c.fetched_at >= w.started_at AND c.fetched_at < w.ended_at
LEFT JOIN replies r ON r.comment_id = c.id
GROUP BY w.label
ORDER BY w.label;
```

## Размеры фактически созданных batch-ов

```sql
WITH selected_batches AS (
    SELECT b.id, count(i.comment_id)::integer AS item_count
    FROM reply_batches b
    JOIN reply_batch_items i ON i.batch_id = b.id
    WHERE b.created_at >= timestamptz '2026-09-08 00:00:00+00'
      AND b.created_at <  timestamptz '2026-09-15 00:00:00+00'
    GROUP BY b.id
)
SELECT item_count, count(*) AS batches, sum(item_count) AS items
FROM selected_batches
GROUP BY item_count
ORDER BY item_count;
```

## Распределение результатов batch-генерации

```sql
SELECT i.status, count(*) AS items
FROM reply_batch_items i
JOIN reply_batches b ON b.id = i.batch_id
WHERE b.created_at >= timestamptz '2026-09-08 00:00:00+00'
  AND b.created_at <  timestamptz '2026-09-15 00:00:00+00'
GROUP BY i.status
ORDER BY i.status;
```

## Маркеры формата во всех непустых текстах и среди опубликованных

```sql
WITH windows AS (
    SELECT 'pre_july'::text AS label,
           timestamptz '2026-07-25 00:00:00+00' AS started_at,
           timestamptz '2026-08-01 00:00:00+00' AS ended_at
    UNION ALL
    SELECT 'post_sep',
           timestamptz '2026-09-08 00:00:00+00',
           timestamptz '2026-09-15 00:00:00+00'
), selected_replies AS (
    SELECT w.label, r.status, r.generated_text
    FROM windows w
    JOIN comments c
      ON c.fetched_at >= w.started_at AND c.fetched_at < w.ended_at
    JOIN replies r ON r.comment_id = c.id
    WHERE coalesce(r.generated_text, '') <> ''
)
SELECT label,
       count(*) AS nonempty_texts,
       count(*) FILTER (
           WHERE position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(generated_text)) > 0
             AND position(chr(1086)||chr(1090)||chr(1074)||chr(1077)||chr(1090)||chr(58) IN lower(generated_text)) > 0
       ) AS type_prefix,
       count(*) FILTER (
           WHERE position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(generated_text)) > 0
             AND position('skip' IN lower(generated_text)) > 0
       ) AS prefixed_skip_token,
       count(*) FILTER (WHERE status = 'published') AS published_texts,
       count(*) FILTER (
           WHERE status = 'published'
             AND position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(generated_text)) > 0
             AND position(chr(1086)||chr(1090)||chr(1074)||chr(1077)||chr(1090)||chr(58) IN lower(generated_text)) > 0
       ) AS published_type_prefix,
       count(*) FILTER (
           WHERE status = 'published'
             AND position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(generated_text)) > 0
             AND position('skip' IN lower(generated_text)) > 0
       ) AS published_prefixed_skip_token
FROM selected_replies
GROUP BY label
ORDER BY label;
```

## Маркеры формата по размеру batch

```sql
SELECT b.item_count,
       count(i.comment_id) AS items,
       count(r.id) FILTER (WHERE r.status = 'published') AS published,
       count(r.id) FILTER (
           WHERE position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(r.generated_text)) > 0
             AND position(chr(1086)||chr(1090)||chr(1074)||chr(1077)||chr(1090)||chr(58) IN lower(r.generated_text)) > 0
       ) AS type_prefix,
       count(r.id) FILTER (
           WHERE position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(r.generated_text)) > 0
             AND position('skip' IN lower(r.generated_text)) > 0
       ) AS prefixed_skip_token,
       count(r.id) FILTER (
           WHERE r.status = 'published'
             AND position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(r.generated_text)) > 0
             AND position(chr(1086)||chr(1090)||chr(1074)||chr(1077)||chr(1090)||chr(58) IN lower(r.generated_text)) > 0
       ) AS published_type_prefix,
       count(r.id) FILTER (
           WHERE r.status = 'published'
             AND position(chr(1090)||chr(1080)||chr(1087)||chr(58) IN lower(r.generated_text)) > 0
             AND position('skip' IN lower(r.generated_text)) > 0
       ) AS published_prefixed_skip_token
FROM reply_batches b
JOIN reply_batch_items i ON i.batch_id = b.id
LEFT JOIN replies r ON r.id = i.reply_id
WHERE b.created_at >= timestamptz '2026-09-08 00:00:00+00'
  AND b.created_at <  timestamptz '2026-09-15 00:00:00+00'
GROUP BY b.item_count
ORDER BY b.item_count;
```

## Причины generation-error по размеру batch

```sql
SELECT b.item_count, b.error_reason, count(*) AS batches
FROM reply_batches b
WHERE b.created_at >= timestamptz '2026-09-08 00:00:00+00'
  AND b.created_at <  timestamptz '2026-09-15 00:00:00+00'
  AND b.status = 'error'
GROUP BY b.item_count, b.error_reason
ORDER BY b.item_count, b.error_reason;
```

## Provider и модель по окнам

```sql
WITH windows AS (
    SELECT 'pre_july'::text AS label,
           timestamptz '2026-07-25 00:00:00+00' AS started_at,
           timestamptz '2026-08-01 00:00:00+00' AS ended_at
    UNION ALL
    SELECT 'post_sep',
           timestamptz '2026-09-08 00:00:00+00',
           timestamptz '2026-09-15 00:00:00+00'
)
SELECT w.label, r.ai_provider, r.ai_model, count(*) AS replies
FROM windows w
JOIN comments c
  ON c.fetched_at >= w.started_at AND c.fetched_at < w.ended_at
JOIN replies r ON r.comment_id = c.id
GROUP BY w.label, r.ai_provider, r.ai_model
ORDER BY w.label, replies DESC, r.ai_provider, r.ai_model;
```

## Проверка непригодной latency-метрики

```sql
WITH windows AS (
    SELECT 'pre_july'::text AS label,
           timestamptz '2026-07-25 00:00:00+00' AS started_at,
           timestamptz '2026-08-01 00:00:00+00' AS ended_at
    UNION ALL
    SELECT 'post_sep',
           timestamptz '2026-09-08 00:00:00+00',
           timestamptz '2026-09-15 00:00:00+00'
), samples AS (
    SELECT w.label,
           extract(epoch FROM r.created_at - c.fetched_at) / 60.0 AS minutes
    FROM windows w
    JOIN comments c
      ON c.fetched_at >= w.started_at AND c.fetched_at < w.ended_at
    JOIN replies r ON r.comment_id = c.id
    WHERE r.created_at IS NOT NULL AND c.fetched_at IS NOT NULL
)
SELECT label,
       round(avg(minutes)::numeric, 1) AS mean_minutes,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY minutes)::numeric, 1)
           AS median_minutes,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY minutes)::numeric, 1)
           AS p90_minutes
FROM samples
GROUP BY label
ORDER BY label;
```

## Детерминированная выборка для ручной оценки

```sql
SELECT r.id, r.status, r.generated_text, c.text, c.thread_text
FROM replies r
JOIN comments c ON c.id = r.comment_id
WHERE c.fetched_at >= timestamptz '2026-09-08 00:00:00+00'
  AND c.fetched_at <  timestamptz '2026-09-15 00:00:00+00'
  AND r.status IN ('generated', 'published', 'error')
ORDER BY md5(r.id::text || 'quality-v1')
LIMIT 12;
```

Для июльской выборки в последнем запросе используются границы `2026-07-25 00:00:00+00` и `2026-08-01 00:00:00+00`. Для выборки из batch размера 3 используется следующий точный запрос:

```sql
SELECT r.id, r.status, r.generated_text, c.text, c.thread_text
FROM reply_batches b
JOIN reply_batch_items i ON i.batch_id = b.id
JOIN replies r ON r.id = i.reply_id
JOIN comments c ON c.id = r.comment_id
WHERE b.created_at >= timestamptz '2026-09-08 00:00:00+00'
  AND b.created_at <  timestamptz '2026-09-15 00:00:00+00'
  AND b.item_count = 3
  AND r.status IN ('generated', 'published', 'error')
ORDER BY md5(r.id::text || 'quality-v1')
LIMIT 12;
```

Тексты из этих выборок не сохраняются в репозитории отчёта, чтобы не публиковать пользовательские комментарии и ответы.
