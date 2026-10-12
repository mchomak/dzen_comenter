# 08 — Восстановление зависшего цикла

**Требования:** G01, D01
**Blocked by:** 05  
**Зона:** `main.py`, `dzen_commenter/bot_health.py`, `dzen_commenter/orchestrator/`, `tests/test_main.py`, `tests/admin/`, `tests/orchestrator/`  
**Волна:** 6  
**Status:** done

## Что должно заработать

Свежий heartbeat долгого цикла остаётся здоровым, а отсутствие прогресса больше 35 минут приводит к безопасному восстановлению worker действующей политикой Docker. Любая операция, которая могла отправить ответ, сначала фиксируется как неопределённая и никогда не повторяется автоматически.

## Из брифа, дословно

> «зависший worker должен восстановиться без ложного heartbeat.»
> «свежий `in_progress` должен считаться здоровым Docker healthcheck, а worker без heartbeat более 35 минут — завершаться»

## Разделы спецификации

Истории 7–9; Решения 3, 8, 11–17; швы Orchestrator/supervisor и Bot health.

## Критерии приёмки

- [x] Свежий `in_progress` проходит healthcheck; heartbeat за пределом порога отображается как `stale`, а не подделывается.
- [x] Fake clock показывает, что worker не завершается при свежем progress и завершается после 35 минут без него; обычная политика `restart: unless-stopped` получает завершившийся процесс.
- [x] Перед выходом после начатого submit сохраняется `publication_unconfirmed`; тест подтверждает отсутствие автоматического повторного submit после перезапуска.
- [x] Watchdog видит активные `reply_id` и `claim_token` из Orchestrator, не завися от возврата зависшего Playwright-вызова; он ждёт durable перехода в `publication_unconfirmed` до завершения процесса.
- [x] Если БД не подтверждает переход в `publication_unconfirmed`, watchdog не заявляет об успешном восстановлении; durable `submitting` остаётся non-retriable до штатного lease recovery, и автоматического повторного submit нет.
- [x] Таймауты основного цикла и keep-alive размечены безопасной фазой/операцией, имеют конечное число повторов и передают подтверждённый renderer crash существующему восстановлению сессии.
- [x] Одиночный `OperationalError` не объявляется проблемой пула и не запускает повтор неопределённой записи; повторяемая ошибка имеет безопасный код причины и операцию без SQL/пользовательских данных.
- [x] Проверки supervisor и health используют fake clock/session и временный снимок состояния; реальные запросы/публикации не выполняются.
- [x] Не применяется `os._exit` или иной путь, обходящий фиксацию результата активной попытки.

## Проверка и ревью

- `python -m pytest -q tests/test_main.py tests/admin tests/orchestrator` — 200 passed.
- `python -m pytest -q` — 615 passed, 51 skipped.
- `python -m ruff check main.py tests/test_main.py` — passed.
- Spec review — clean. Craft review — non-blocking MEDIUM note: retry-boundary tests share the attempt-limit constant with their expected call count.
