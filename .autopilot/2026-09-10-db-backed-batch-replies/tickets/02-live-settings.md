# 02 — Live settings

**Требования:** R04, R06
**Blocked by:** —
**Зона:** `dzen_commenter/config/`, `dzen_commenter/admin/`, `tests/config/`, `tests/admin/`
**Волна:** 1
**Status:** ready

## Что должно заработать

Оператор может менять несекретные cooldown и limit повторной публикации через существующий runtime JSON и форму настроек; отсутствующие ключи используют безопасные defaults.

## Критерии приёмки

- [ ] Runtime parser валидирует и возвращает оба publication retry параметра с defaults.
- [ ] Admin validation/form сохраняет только допустимые значения и не затрагивает секреты.
- [ ] Тесты runtime config и admin покрывают defaults, reload и невалидный ввод.
