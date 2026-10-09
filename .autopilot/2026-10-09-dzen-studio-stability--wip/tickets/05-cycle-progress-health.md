# 05 — Heartbeat прогресса долгого цикла

**Требование:** R07  
**Зависит от:** 01, 04  
**Зона:** `dzen_commenter/bot_health.py`, `dzen_commenter/dzen/page.py`, `main.py`, `tests/admin/`, `tests/dzen/`, `tests/test_main.py`  
**Волна:** 4  
**Status:** done

## Результат

Различать работающий долгий проход Studio и остановившийся worker. Supervisor записывает активный цикл перед запуском, сканер обновляет heartbeat между проходами и при извлечении комментариев; после результата supervisor снимает признак активного цикла. Свежий `in_progress` считается здоровым для Docker, а отсутствие нового heartbeat за прежний порог приводит к `stale`.

## Проверки

- `python -m pytest -q tests/admin/test_bot_health.py tests/dzen/test_dzen_page.py::test_fetch_comments_refreshes_health_progress_for_each_feed_pass tests/test_main.py` — 34 passed.
- Полный `python -m pytest -q` — 597 passed, 51 skipped после добавления Chromium regression-теста пакетного снимка.
- `python -m ruff check dzen_commenter/bot_health.py dzen_commenter/dzen/page.py main.py` — passed.
- На первом production-проходе прежний worker завершил 897 карточек и 1 994 комментария за 16 минут; это обосновало progress heartbeat. Финальный выпуск и healthcheck по новой версии проверяются по R06/T04.
