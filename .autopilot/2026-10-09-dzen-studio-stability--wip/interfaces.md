# Interfaces

## Границы, решённые в спецификации

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| DzenStudioPage | Загрузка ленты, поиск комментария и раскрытие веток | Существующие fetch_comments и publish_reply; приватный поиск принимает URL публикации как необязательный фильтр | DOM-обходы, идентичность контролов и метрики |
| PlaywrightSessionManager | Жизненный цикл Chromium | recover_if_browser_crashed(exception) -> bool | Детали закрытия и пересоздания контекста |
| database engine factory | Параметры SQLAlchemy соединений | create_database_engine(url) -> Engine | Настройки пула |
| Orchestrator/supervisor | Очереди публикаций и восстановление после ошибки | Существующие fail_publication / mark_publication_unconfirmed; потокобезопасный снимок активной попытки `{reply_id, claim_token}` | Claim tokens и transition rules |
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

## Для продолжения работ по G01

- Studio scan сохраняет существующую конечную fail-closed семантику; общий бюджет равен 30 минутам. `progress_callback` вызывается по ходу обработки групп, а после DOM re-render допускается один повторный поиск только с проверкой идентичности.
- Supervisor принимает только свежий heartbeat как progress; после 35 минут без progress завершает зависший цикл. Перед выходом он обязан сохранить существующее состояние `publication_unconfirmed`, если submit мог начаться; автоматического повторного submit нет.
- Orchestrator публикует thread-safe снимок активной попытки `{reply_id, claim_token}` после durable `submitting`. Watchdog перед завершением процесса вызывает существующий переход в `publication_unconfirmed` и дожидается результата БД. Если БД недоступна, watchdog не объявляет безопасную фиксацию успешной и не делает повтор submit; durable submitting claim остаётся non-retriable до существующего lease recovery. Запрещены обход фиксации через `os._exit` и надежда на остановку зависшего Playwright-потока.
- Для production-ошибок используются только структурированные безопасные поля (event/phase/reason/count/duration), без данных публикаций. Один `OperationalError` не классифицируется как проблема пула без повторяемого подтверждения.
- Швы для изменений: DzenStudioPage на fake-page/monotonic clock; run_supervised на fake session/clock; Orchestrator с fake repository/claim token; health endpoint на временных heartbeat snapshots. Не запускать реальную публикацию.
## Из таска 07 — устойчивое чтение Studio

- Полный проход раскрытия ответов имеет общий конечный бюджет 30 минут и обновляет `progress_callback` по обработке групп.
- Ошибки расширения контролов дают безопасные structured events `studio_reply_expansion_incomplete` и `studio_reply_expansion_control_snapshot_deferred` с `failure_phase`, `duration_ms` и техническими счётчиками; тексты/авторы/URL/HTML не логируются.
- После stale/изменённого DOM-контрола разрешается один повторный поиск с проверкой идентичности публикации/ветки/контрола; неоднозначная отправка не повторяется.
- Тестовый шов: fake Studio page и управляемый monotonic clock.
## Из таска 08 — безопасное завершение зависшего цикла

- Orchestrator exposes `active_publication_attempt()` and `mark_active_publication_unconfirmed(*, reason)`; active `{reply_id, claim_token}` is published under a lock only after durable `submitting`.
- `get_bot_health(..., stale_after_seconds=...)` allows a finite testable stale threshold; `run_supervised` accepts injected clock/wait/termination functions.
- Watchdog runs independently of the blocking cycle/keep-alive, waits for durable `publication_unconfirmed` before termination, and never retries submit. If the database write fails, the durable submitting claim remains non-retriable until existing lease recovery.
- Test seam: fake repository, clock, wait and termination functions; no live Dzen calls.

## Из таска 09 — live scan без удержания DOM handles

- На live Playwright-пути comment counts берутся без накопления Python-списков `ElementHandle` между scan passes; content extraction остаётся одним сериализованным DOM-снимком.
- Fake/non-live fallback продолжает собирать `COMMENT_NODE` handles, когда live snapshot API недоступен.
- Тестовый шов: fake live page с snapshot/counts и non-live page с обычным DOM query; проверяются точность счётчиков, отсутствие live handles и сохранность fallback.
