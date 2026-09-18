# 01 — Durable generation queue and statuses

**Требования:** R01, R03, R04, R05, R06, R07, R08, R09
**Blocked by:** —
**Зона:** `dzen_commenter/contracts/`, `dzen_commenter/db/`, `tests/db/`, `tests/orchestrator/conftest.py`
**Волна:** 1
**Status:** ready

## Что должно заработать

После отказа от batch у каждого comment есть одна durable generation job. Она сохраняет retry metadata и совершает атомарные переходы generation/reply/comment; batch tables остаются историческими и не являются частью нового API.

## Из брифа, дословно

> «Статусы меняем сразу в БД по ходу дела»
> «при ошибках ретрай через заданное в настройках время»
> «если ошибка была в генерации ответа, то генерируем заново»
> «если ИИ говорит, что комментарий стоит пропустить, то пропускаем без ретрая и даем ему соответсвующий статус»

## Разделы спецификации

Истории 1, 3–7; «Очередь generation»; «Конечный автомат статусов».

## Критерии приёмки

- [ ] Additive migration creates `reply_generation_queue` with unique active relationship, retry metadata and a ready index; it neither drops nor rewrites batch-era rows/tables.
- [ ] Repository idempotently enqueues an eligible comment, claims one earliest ready item with lock-safe semantics, and exposes only single-generation operations to the orchestrator contract.
- [ ] Completion atomically distinguishes reply, explicit skip and generation error; retry schedules the configured next attempt, terminal generation failure and skip are not represented as batch outcomes.
- [ ] Existing publication retry continues to retain generated reply text and records transparent comment/reply state without using `skipped` for a technical failure.
- [ ] Focused PostgreSQL and fake-repository tests are written red first and pass; no ticket commit is created.
