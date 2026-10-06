# T35 Защита публикации от дублей

Требования: R01–R09 из `../manifest.md`.

Авторитетный план этапа и двоичные критерии приёмки: `[[35-publication-unconfirmed-duplicate-guard]]` в Obsidian, `Projects/Work/dzen-comenter/notes/35-publication-unconfirmed-duplicate-guard.md`.

Зоны: `dzen_commenter/dzen/`, `dzen_commenter/db/`, `dzen_commenter/orchestrator/`, `dzen_commenter/contracts/`, `dzen_commenter/admin/` и прямо относящиеся тесты.

Выполнение: coder → stage commit → независимый tester PASS → push `main` → production backup/deploy/health. Исторические ошибки без признака принятой отправки не переклассифицировать автоматически.
