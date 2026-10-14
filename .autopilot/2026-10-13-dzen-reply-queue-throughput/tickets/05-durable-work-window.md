# 05 — Ограниченное окно и очередь публикаций

**Требования:** R01, R01.3, R01.4, R01.5, G03, G04, G05, A05, A06, A07, A09, A14, A15
**Blocked by:** 01, 02
**Зона:** `dzen_commenter/db/`, `dzen_commenter/contracts/`, `tests/db/`, `tests/contracts/` (кроме существующего `test_foundation.py`; этот файл закреплён за Ticket 06 для синхронизации Settings-контракта)
**Волна:** 3
**Status:** done

## Что должно заработать

Бот держит не более 30 активных generation/publication jobs суммарно. Сохранённые готовые ответы занимают слоты перед генерацией и публикуются без повторного обращения к модели. Слишком старые комментарии закрываются с объяснимой причиной, а старый хвост не переполняет активную выборку.

## Из брифа, дословно

> «последние 30 свежих комментариев (включая ретраи)»
> «сохранённые ответы направил на публикацию, свежие комментарии допустил в новую рабочую выборку, устаревшие завершил с явной причиной»

## Разделы спецификации

Истории 3–6 и 10–12; решения «Активное окно», «Работа вне окна», «Просрочка» и «Сохранение результата»; шов Repository.

## Критерии приёмки

- [x] `WorkWindowSnapshot` и `reconcile_work_window(now, stale_before, limit)` реализованы одной идемпотентной транзакцией; число уникальных comment IDs с активной queued/claimed/submitting generation или publication стадией, включая delayed retry, не превышает limit. Переход generation → publication не занимает второй слот.
- [x] Для каждого Reply `generated` без publication row восстанавливается задание публикации; publication jobs получают свободные слоты раньше generation. При большом хвосте сохранённые публикации ждут свободного слота без повторной генерации.
- [x] На первой reconciliation самые свежие eligible legacy generation jobs попадают в свободные слоты; избыток получает `waiting` и не активируется повторно. Свежесть — `coalesce(posted_at, first fetched_at) DESC`, затем стабильный comment id.
- [x] `admit_recent_comments` сохраняет и ставит в очередь только неизвестные card IDs, в количестве свободных слотов; дубликаты и непоместившиеся карточки не добавляются в БД. Существующие активные retry сохраняют слот, `attempt_count` и `next_attempt_at`.
- [x] Старые `new`/`waiting`/`queued` generation и publication jobs закрываются как `skipped` с `Reply.error_reason`; старый claim закрывается только после истечения lease; `submitting` с неизвестным исходом не повторно отправляется и сохраняет `unconfirmed`.
- [x] Успешная generation атомарно создаёт Reply и publication row, закрывает generation claim; ошибка транзакции не оставляет частичную пару. Не добавляется миграция или удаление бизнес-строк.
- [x] PostgreSQL tests покрывают лимит 30 на границе, уникальность лимита comment IDs при смене стадии, priority publications, сохранение retry полей, initial legacy selection, новые unseen IDs, stale/claim expiry, неизвестную публикацию, идемпотентность и atomic commit.
