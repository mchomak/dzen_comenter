# 01 — Persistence and migration

**Требования:** R01, R02, R04, R05, R06
**Blocked by:** —
**Зона:** `dzen_commenter/db/`, `dzen_commenter/contracts/`, `tests/db/`, `tests/contracts/`
**Волна:** 1
**Status:** ready

## Что должно заработать

Repository может атомарно сохранить/получить article cache, ставить валидные generated replies в отдельную publication-очередь, claim-ить и retry-ить публикацию без повторной генерации. Alembic добавляет только новые nullable поля, таблицу и индексы.

## Критерии приёмки

- [ ] Новая миграция upgrade/downgrade создаёт article cache поля и `reply_publication_queue`, не обновляя существующие rows.
- [ ] Публикационный claim использует стабильный порядок и `FOR UPDATE SKIP LOCKED`; один reply не может получить два активных элемента очереди.
- [ ] Ошибка публикации создаёт только publication retry с cooldown/attempt limit; ответ и generation queue не создаются повторно.
- [ ] Исчерпание publication retry сохраняет диагностическую причину и терминальный `error`, но не `skipped`.
- [ ] Тесты repository и migration покрывают эти случаи на fake/in-memory seams, без production DB.
