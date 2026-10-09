# Interfaces

## Границы, решённые в спецификации

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| DzenStudioPage | Загрузка ленты, поиск комментария и раскрытие веток | Существующие fetch_comments и publish_reply; приватный поиск принимает URL публикации как необязательный фильтр | DOM-обходы, идентичность контролов и метрики |
| PlaywrightSessionManager | Жизненный цикл Chromium | recover_if_browser_crashed(exception) -> bool | Детали закрытия и пересоздания контекста |
| database engine factory | Параметры SQLAlchemy соединений | create_database_engine(url) -> Engine | Настройки пула |
| Orchestrator/supervisor | Очереди публикаций и восстановление после ошибки | Существующий fail_publication / mark_publication_unconfirmed | Claim tokens и transition rules |
| Bot health | Свежесть последнего heartbeat и завершённый результат цикла | `write_bot_progress` сохраняет `cycle_in_progress`; `/health/bot` возвращает `in_progress`, пока timestamp свежий | Файл snapshot и атомарная замена |

## Правила проекта

- Python 3.11, Playwright sync API, SQLAlchemy 2, FastAPI, PostgreSQL, pytest; новых зависимостей не добавлять.
- Проверки Windows запускаются из корня: .venv\Scripts\python.exe -m pytest -q. Узкие тесты запускаются тем же интерпретатором с путём к тестовому файлу.
- Не проверять изменения реальной авторизацией или публикацией на Dzen; использовать fakes и injected-клиенты.
- Не менять схему PostgreSQL, данные, browser profile, storage state или переменные окружения.
- Не писать секреты, тексты комментариев, имена авторов, адреса постов, токены или HTML в новые диагностические логи.
- При неясном внешнем контракте остановиться и вернуть BLOCKED, а не добавлять новую зависимость или менять поведение отправки.
- Существующая защита publication_unconfirmed и positive evidence на границе Dzen обязательны. Ошибка до submit может быть retryable; после потенциального submit неопределённый результат не отправляется повторно автоматически.

## Из таска 02 — настройки PostgreSQL

- `create_database_engine(url: str) -> Engine` — общий SQLAlchemy engine factory с `pool_pre_ping=True` и `pool_recycle=1800`; используется worker и admin.

## Из таска 01 — поиск Studio

- `_find_comment_node_with_scroll(comment_id, *, reply_id=None, post_url=None)` — ограничивает поиск известной публикацией.
- Полный сбор раскрывает ответы только в новых DOM-группах; structured event `studio_feed_scan` сохраняет длительность и технические счётчики без данных публикации.
- На live Playwright `fetch_comments` читает карточки и все поля комментариев одной read-only `page.evaluate` DOM-снимком; ID, relative-time parsing и thread context формируются в Python. Fake-page fallback оставляет обычный query-путь для unit tests.

## Из таска 03 — восстановление Chromium

- `SessionManager.recover_if_browser_crashed(exception: Exception) -> bool` — восстанавливает persistent context после подтверждённого падения; supervisor вызывает его после неудачного цикла и не повторяет текущую публикацию.
- `DzenStudioPage` принимает необязательный progress callback и обновляет его в начале/между проходами Studio и во время извлечения комментариев; callback пишет только техническое состояние, без текста комментариев или данных авторов.
- `get_bot_health` читает опциональный `cycle_in_progress` для обратной совместимости со старыми snapshots. Свежий цикл получает статус `in_progress`, который считается успешным для Docker healthcheck; устаревший heartbeat получает `stale`.

## Из таска 04 — ограниченный поиск источника

- `publication_source_comment_search_completed` сообщает `scan_limit_reached` и `absence_confirmed` независимо: исчерпанный лимит передаёт `true` и `false` соответственно.
