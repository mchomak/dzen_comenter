# 05 — Publication lease ownership and complete repository contract

**Требования:** R06, R09, D01
**Blocked by:** 01
**Зона:** `dzen_commenter/contracts/`, `dzen_commenter/db/`, `dzen_commenter/orchestrator/`, `tests/db/`, `tests/orchestrator/`
**Волна:** 4
**Status:** ready

## Что должно заработать

Generation и publication queue имеют одинаковую lease-ownership гарантию: устаревший worker не может завершить retry, уже re-claimed новым worker. Public repository Protocol содержит каждый метод, который вызывает orchestration.

## Из брифа, дословно

> «если ответ сгенерировался, а ошибка в публикации, то генерацию по новой не запускаем, просто пытаемся через время заново опубликовать»
> «статусы должны быть прозрачными и корректными»

## Разделы спецификации

Истории 4, 6; «Конечный автомат статусов».

## Критерии приёмки

- [ ] `ClaimedPublication` carries an opaque claim token and completion/failure use it conditionally, so an old token is rejected after re-claim.
- [ ] `CommentRepository` declares every single-flow operation used by `OrchestratorLoop`, including atomic upsert, recovery enqueue and conditional skip.
- [ ] DB tests cover stale publication token rejection for complete and failure; generation stale-token tests cover complete, skip and failure.
- [ ] Focused tests are red first then green; full suite remains green. No commit is made by this ticket.
