# 03 — Single-comment orchestration and output contract

**Требования:** R01, R02, R03, R05, R06, R07, R08, R09, R11, R12
**Blocked by:** 01, 02
**Зона:** `dzen_commenter/orchestrator/`, `dzen_commenter/prompt/`, `dzen_commenter/contracts/`, `main.py`, `tests/orchestrator/`, `tests/prompt/`, `tests/test_migrations.py`
**Волна:** 2
**Status:** ready

## Что должно заработать

`run_cycle` processes one queued comment at a time: collect/enqueue, claim/generate, then claim/publish. No batch prompt, parser or batch method is reachable. AI failures retry generation; publication failures retry only the saved reply.

## Из брифа, дословно

> «появился комментарий - сгенерировали ответ - опубликовали»
> «если ответ сгенерировался, а ошибка в публикации, то генерацию по новой не запускаем»
> «в ответ чаще попадают слова, которых там быть не должно, например "ответ" или "skip" и похожие»
> «используя наш алгоритм обрезания статей для экономии токенов»

## Разделы спецификации

Истории 1–2, 4–8, 10; «Конечный автомат статусов»; «Prompt и article context».

## Критерии приёмки

- [ ] Constructor/wiring and `run_cycle` have no active batch branch or batch provider/parser dependency; process quota and age/own-comment checks before generation enqueue/claim.
- [ ] A reply outcome persists the cleaned/cached article-context result and publication job, then publication is attempted in the same cycle; a restart later can claim either pending phase safely.
- [ ] Explicit `SKIP` is terminal without retry; empty/protocol-only AI text retries generation rather than becoming skipped or public text; recognised leading legacy labels cannot reach `publish_reply`.
- [ ] A publication exception leaves its generated text unchanged and schedules only publication retry; a generation exception performs a fresh AI attempt only after its own cooldown.
- [ ] Regression tests demonstrate the complete single path, both retry separations, no batch methods, marker suppression and preserved article sanitation; tests are written red first and pass.
