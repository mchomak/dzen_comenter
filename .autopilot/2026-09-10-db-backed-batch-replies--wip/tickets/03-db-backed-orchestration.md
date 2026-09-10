# 03 — DB-backed orchestration

**Требования:** R01, R02, R03, R04, R05
**Blocked by:** 01, 02
**Зона:** `dzen_commenter/orchestrator/`, `tests/orchestrator/`, `tests/prompt/`
**Волна:** 2
**Status:** ready

## Что должно заработать

Цикл формирует batch из БД независимо от текущего DOM snapshot, создаёт timeout partial batch, кеширует контекст статьи и отделяет публикацию от генерации. Ошибку DOM-публикации он повторяет через publication queue, без AI-вызова и без ложного skip.

## Критерии приёмки

- [ ] Eligible comment, отсутствующий в текущем snapshot, может быть сгенерирован из сохранённых DB данных после timeout partial batch.
- [ ] Новый article context извлекается и кешируется один раз; failure не блокирует безопасный generation fallback.
- [ ] Валидный reply ставится в publication queue, а не публикуется как побочный эффект generation save.
- [ ] Publication `LookupError`/browser error даёт retry; AI calls и generated text не дублируются, `skipped` не записывается.
- [ ] Успех публикации и исчерпание retry корректно завершают разные статусы.
- [ ] Тесты используют существующие injected fakes и не запускают browser/real Dzen.
