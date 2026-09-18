# 02 — Live retry settings without batch controls

**Требования:** R01, R04, R07, R09
**Blocked by:** —
**Зона:** `dzen_commenter/config/`, `dzen_commenter/admin/`, `tests/config/`, `tests/admin/`
**Волна:** 1
**Status:** ready

## Что должно заработать

Панель и runtime JSON перестают предлагать batch. Оператор настраивает отдельные generation и publication retry, а старый JSON с batch keys безопасно читается без возвращения batch режима.

## Из брифа, дословно

> «уберем батчи вообще, просто отключим их»
> «при ошибках ретрай через заданное в настройках время»
> «статусы должны быть прозрачными и корректными»

## Разделы спецификации

Истории 1, 4–6; «Runtime/admin configuration».

## Критерии приёмки

- [ ] Runtime settings remove batch enable/cutover/size/wait/retry fields and add bounded hot-reloaded generation cooldown and attempt limit.
- [ ] Legacy persisted batch keys are ignored rather than activating batch or invalidating the runtime configuration.
- [ ] Settings validation/page render only generation/publication retry controls and preserve the existing non-batch settings.
- [ ] Focused config and admin tests are written red first and pass; no ticket commit is created.
