# 08 — Push, безопасная выкладка и production оценка

**Требования:** R02, R05, R06i, G03, G04, G05, G06, G07, G08, A03, A04, A11
**Blocked by:** 05, 06, 07, 09
**Зона:** production host / release operations
**Волна:** 5
**Status:** review

## Что должно заработать

Проверенный код попадает в текущую ветку и на сервер без потери production базы. На новом цикле сохранённые ответы направляются к публикации, в активную выборку входят свежие задания до лимита, а просроченный хвост получает явную причину. После запуска снимаются запрошенные показатели.

## Из брифа, дословно

> «коммит пуш и деплой»
> «сохранённые ответы направил на публикацию, свежие комментарии допустил в новую рабочую выборку, устаревшие завершил с явной причиной»
> «после запуска оценивал бы результат по времени „обнаружен → опубликован“, количеству готовых ответов, ожидающих публикации, и числу комментариев, которые устарели в ожидании»

## Разделы спецификации

Истории 13–16; решения «Метрики» и «Релиз»; Production operations boundary; assumptions A03–A04, A11.

## Критерии приёмки

- [ ] Все code ticket commits и run artifacts опубликованы fast-forward push в текущую ветку; история не переписывается, существующие посторонние untracked artifacts не стадируются.
- [ ] Перед изменением production сделан timestamped PostgreSQL backup; `pg_restore --list` подтверждает читаемость архива. Секреты и пользовательские тексты не попадают в вывод отчёта.
- [ ] Старый app worker остановлен до сборки/миграции; `postgres` volume, session/config/browser volumes и rollback images сохранены. Одновременно работает только один worker.
- [ ] Новая версия стартует на pushed commit, миграции (если есть) применяются успешно; app health/heartbeat, admin HTTP и активная schema revision подтверждены.
- [ ] После старта saved Reply `generated` имеют publication jobs без новой генерации; snapshot подтверждает active generation + publication ≤30; просроченные записи имеют terminal status и причину; данные комментариев и PostgreSQL volume сохранены.
- [ ] Read-only production queries после запуска сообщают среднее/медиану discovery→confirmed publication с периодом и размером выборки, count generated Reply со статусом pending publication и stale completions. При менее 30 публикациях или периоде менее 24 часов оценка лимита 30 помечена предварительной.
- [ ] Кэш и swap повторно не меняются: эти действия уже завершены предыдущей выкладкой; production server не очищается и volumes не удаляются.

## Production evidence for independent verification

The first tester could verify only the pushed branch and HTTP responses because SSH timed out and the production checks were described only in this note. The sanitized command outputs, aggregate SQL results, and explicit active-window definition are now persisted in [production-evidence-2026-10-14.md](../production-evidence-2026-10-14.md). No comment text, author, URL, credentials, row identifiers, or HTML is included.

Latest snapshot (`2026-10-14T12:07:41Z`): app image `dzen_commenter-app:4cd5534` is healthy and runs exact pushed SHA `4cd553439874c51b4da13a1c48738bf9346b6f54`; admin `/login` returns 200; PostgreSQL is healthy on the preserved volume and schema is at `0012_ai_call_reservations`. The backup is readable by `pg_restore --list`, the rollback image remains available, and one app worker is running.

Queue counts are `queued=28`, `claimed=2`, `waiting=21`; only queued and claimed work occupies the 30 active slots, so 21 waiting items are outside the window. There are 51 generated replies pending publication and none without a publication job. Since release, 16 comments were fetched (15 generated and one skipped); the 15 new publication jobs are persisted in `waiting` while prior publication work fills the active window. There have been 21 confirmed publications after release, all for comments fetched before release; average discovery-to-publication is 21,304 seconds and median is 20,688 seconds. The 25 min 37 sec observation and 21-publication sample make the throughput estimate preliminary.

The first reconciliation completed 501 stale records with explicit reasons. The earlier sanitized Studio telemetry and the current full backup/queue/latency evidence are recorded in the linked report. No cache cleanup, swap change, data-volume removal, restore, or PostgreSQL recreation was performed during this redeploy.
