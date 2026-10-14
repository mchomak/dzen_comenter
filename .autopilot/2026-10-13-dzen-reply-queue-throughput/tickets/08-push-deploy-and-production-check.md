# 08 — Push, безопасная выкладка и production оценка

**Требования:** R02, R05, R06i, G03, G04, G05, G06, G07, G08, A03, A04, A11
**Blocked by:** 05, 06, 07, 09
**Зона:** production host / release operations
**Волна:** 5
**Status:** in progress

## Что должно заработать

Проверенный код попадает в текущую ветку и на сервер без потери production базы. На новом цикле сохранённые ответы направляются к публикации, в активную выборку входят свежие задания до лимита, а просроченный хвост получает явную причину. После запуска снимаются запрошенные показатели.

## Из брифа, дословно

> «коммит пуш и деплой»
> «сохранённые ответы направил на публикацию, свежие комментарии допустил в новую рабочую выборку, устаревшие завершил с явной причиной»
> «после запуска оценивал бы результат по времени „обнаружен → опубликован“, количеству готовых ответов, ожидающих публикации, и числу комментариев, которые устарели в ожидании»

## Разделы спецификации

Истории 13–16; решения «Метрики» и «Релиз»; Production operations boundary; assumptions A03–A04, A11.

## Критерии приёмки

- [x] Все code ticket commits и run artifacts опубликованы fast-forward push в текущую ветку; история не переписывается, существующие посторонние untracked artifacts не стадируются.
- [x] Перед изменением production сделан timestamped PostgreSQL backup; `pg_restore --list` подтверждает читаемость архива. Секреты и пользовательские тексты не попадают в вывод отчёта.
- [x] Старый app worker остановлен до сборки/миграции; `postgres` volume, session/config/browser volumes и rollback images сохранены. Одновременно работает только один worker.
- [x] Новая версия стартует на pushed commit, миграции (если есть) применяются успешно; app health/heartbeat, admin HTTP и активная schema revision подтверждены.
- [x] После старта saved Reply `generated` имеют publication jobs без новой генерации; snapshot подтверждает active generation + publication ≤30; просроченные записи имеют terminal status и причину; данные комментариев и PostgreSQL volume сохранены.
- [x] Read-only production queries после запуска сообщают среднее/медиану discovery→confirmed publication с периодом и размером выборки, count generated Reply со статусом pending publication и stale completions. При менее 30 публикациях или периоде менее 24 часов оценка лимита 30 помечена предварительной.
- [x] Кэш и swap повторно не меняются: эти действия уже завершены предыдущей выкладкой; production server не очищается и volumes не удаляются.

## Production evidence for independent verification

The first tester could verify only the pushed branch and HTTP responses because SSH timed out and the production checks were described only in this note. The sanitized command outputs, aggregate SQL results, and explicit active-window definition are now persisted in [production-evidence-2026-10-14.md](../production-evidence-2026-10-14.md). The report also records the rollout command order (old app stop → backup → image build → app start), timestamped backup/image/container metadata, the identical pre/post PostgreSQL container ID, and the pre-existing volume creation times. Shell history has no timestamp for the exact stop second; this limitation is stated explicitly. No comment text, author, URL, credentials, row identifiers, or HTML is included.

The snapshot at `2026-10-14T12:07:41Z` is historical, not the latest. Its database cohort was corrected after identifying that the timestamp columns are naive Moscow wall-clock values: the correct interval contains 13 confirmed publications and 0 newly fetched comments (not 21 and 16). The production evidence report records the corrected counts, the withdrawn status breakdown, and the query cutoffs.

The later Ticket 10 snapshot (`2026-10-14T13:32:44Z`) is the latest production evidence: app/admin run image `dzen_commenter-app:44c29b8`, app is healthy, admin `/login` returns 200, PostgreSQL remains healthy with the same container ID and named volumes, schema remains `0012_ai_call_reservations`, and exactly one authenticated worker is healthy. It reports 26 generated replies waiting for publication, 0 without a publication job, 11 comments fetched since the queue rollout, and 501 stale completions with an explicit age reason. Since rollout there were 31 confirmed publications; discovery-to-publication averaged 13,191 seconds and had a 14,055-second median. The observation lasted 1 h 50 min 40 sec, so the sample meets 30 publications but remains preliminary until 24 hours have elapsed. See [production-evidence-2026-10-14.md](../production-evidence-2026-10-14.md) for the backup checksum, deployment details, and query definitions.

No cache cleanup, swap change, data-volume removal, restore, or PostgreSQL recreation was performed during these redeploys; cache cleanup and the 2 GiB additional swap were completed earlier in the run.
