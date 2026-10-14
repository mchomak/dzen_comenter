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

Read-only checks, sanitized logs and aggregate SQL only; no comment text, author, URL, credentials or HTML included.

- Code commit `4cd553439874c51b4da13a1c48738bf9346b6f54` was pushed fast-forward to `codex/studio-duplicate-post-identity`. App and admin use image `dzen_commenter-app:4cd5534`; `/app/SOURCE_COMMIT` contains that exact SHA. Previous image `dzen_commenter-app:832a4cb` remains available.
- Before redeploy, backup `/root/backups/dzen_commenter-20261014-ticket09-before-redeploy.dump` was created: 3,151,546 bytes, mode `600`; `pg_restore --list` recognized a CUSTOM archive with 70 TOC entries.
- The worker was stopped before build. New app is healthy, exactly one app worker is running, admin `/login` returns HTTP 200, and `/health/bot` returned `operational` with heartbeat `2026-10-14T11:59:24Z`. The PostgreSQL container ID stayed `82bfa98ec907e2fbb30614b309757d6e936c5768f1fa77b78524f3a952492e17`, volume `dzen_commenter_pgdata` stayed mounted, and schema remains `0012_ai_call_reservations`. App browser/session/config/login-control volumes retained their existing names.
- From 11:42 UTC through 11:59 UTC, 31 publication source searches scanned at most 100 groups each; median duration was 1,719.13 ms, maximum 3,813.81 ms. There were 13 retryable `source_comment_not_found` results and 11 confirmed/completed publications. No deep fallback was observed.
- At 11:59:50 UTC: active unique comment IDs `30` (limit `30`); generated replies pending publication `54`; generated replies without a publication job `0`; new comments fetched since rollout `0`; total published replies `2,076`.
- For confirmed replies published since 14:42 local time: sample `11`, average discovery-to-publication `18,446.4` seconds, median `18,093.7` seconds. The observation period was about 18 minutes and the sample is old backlog, so both the metric and the conclusion about window size remain preliminary (<24 hours and <30 publications).
- During the first reconciliation in this run, stale skipped replies rose from `2,354` to `2,855` (`501` newly completed); all 501 had an explicit reason. Current aggregate: `1,511` skipped replies with a reason and `1,344` historical skipped replies without one.
- The post-rollout Studio scans each read 100 groups; the aggregate query found no comments fetched after rollout. No cache cleanup, swap change, data volume removal or PostgreSQL recreation was performed during this redeploy.
