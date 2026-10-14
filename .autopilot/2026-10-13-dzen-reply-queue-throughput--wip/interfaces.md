# Общий контракт реализации

## Границы, решённые в спецификации

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| Оркестратор | Порядок этапов и ограниченный drain, короткий scan schedule | `run_cycle()` | Due selection на уровне цикла, error continuation и расписание |
| AI providers/settings | Request timeout к моделям | `AI_REQUEST_TIMEOUT_SECONDS` и provider factory | HTTP клиент, retry connect и формирование запросов |
| Repository | Durable work window, admission, claim leases, quota reservations, retry, expiry reason | `reconcile_work_window(now, stale_before, limit) -> WorkWindowSnapshot` и текущие generation/publication methods | SQL, транзакции и переходы `waiting ↔ queued` |
| Studio adapter | Обновление текущей Studio page, short DOM scan и ответы | `fetch_recent_comments(max_cards=100)` | Навигация страницы, селекторы, прокрутка и раскрытие скрытых ответов |
| Production operations | Backup/rollout и разовое измерение после старта | Runbook plus read-only SQL | Host access и secrets |

## Repository: окно, выборка и статусы

Snapshot:

```python
@dataclass(frozen=True)
class WorkWindowSnapshot:
    active_generation: int
    active_publication: int
    waiting_generation: int
    waiting_publication: int
    generated_waiting_publication: int
    expired_generation: int
    expired_publication: int
```

Основные операции:

```python
reconcile_work_window(
    *, now: datetime, stale_before: datetime, limit: int
) -> WorkWindowSnapshot

admit_recent_comments(
    comments: Sequence[Comment], *, queued_at: datetime, limit: int
) -> int
```

`reconcile_work_window` — одна идемпотентная транзакция:

1. Восстанавливает отсутствующие publication rows для Reply со статусом `generated`.
2. Expire: `new`/`waiting`/`queued` jobs с возрастом старше cutoff становятся terminal `skipped`; сохраняется читаемый `Reply.error_reason`, Comment остаётся в БД. Возраст — `coalesce(posted_at, first fetched_at)`. Действующий `claimed` не завершать; просроченный claim закрывать только после истечения lease. Публикация `submitting` с неизвестным исходом остаётся `unconfirmed`.
3. В активное окно входят только уникальные `comment_id`, для которых активна хотя бы одна generation/publication стадия в состояниях `queued`, действующий `claimed` или `submitting`; отложенные retries считаются активными. Один комментарий, переходящий из generation в publication, занимает один слот. Уникальных comment IDs не больше `limit`. Сохранённые ответы занимают свободные места раньше генерации; `waiting` publication rows активируются по мере появления места. Retry активных generation jobs сохраняют слот, `attempt_count` и `next_attempt_at`.
4. При первой reconciliation старые eligible generation rows выбираются по свежести до остатка активных слотов; избыток переходит в `waiting` и не активируется повторно. После старта `waiting` generation rows не поднимаются автоматически. Это ограничивает legacy хвост, но не теряет его историю.
5. `admit_recent_comments` сортирует `fetch_recent_comments` по `coalesce(posted_at, fetched_at) DESC, comment_id` для детерминированного отбора; сохраняет и ставит в очередь только неизвестные IDs в количестве свободных мест после учёта активных уникальных IDs. Дубликаты и непомещённые карточки не добавляет в БД; их можно увидеть в следующем short scan.

Существующие границы claims сохраняются:

- `claim_next_generation(now) -> ClaimedGeneration | None` выдаёт только due active `queued` jobs.
- `complete_generation(...)` атомарно сохраняет Reply `generated`, publication row и закрывает generation claim.
- `claim_next_publication(now) -> ClaimedPublication | None` выдаёт только due active `queued` publication jobs.
- `complete_publication(...)`, `fail_publication(...)`, `mark_publication_unconfirmed(...)` сохраняют claim-token checks и существующие safe transitions.
- Generation + первая немедленная publication attempt считают одним `MAX_REPLIES_PER_CYCLE` item; publication-only work/retry считает один item. При сбое записи уже созданного текста сначала повторить тот же persistence; generation завершена только после durable commit Reply + publication row.

Новая миграция не ожидается: `state` — существующее текстовое поле. Если реализация покажет, что новое schema field неизбежно, исполнитель возвращает `BLOCKED` с причиной и не добавляет миграцию без пересогласования контракта.

## AI providers/settings

- `Settings.MAX_ACTIVE_WORK_ITEMS: int` — default 30 (A16), только положительное значение; orchestrator использует его как capacity уникальных активных comment IDs.
- `Settings.AI_REQUEST_TIMEOUT_SECONDS: float` — default 90 секунд (A01), значение должно быть больше нуля.
- Provider factory передаёт timeout в активные HTTP providers OpenAI-compatible и GigaChat; connect timeout не больше общего. YandexGPT пока заглушка.
- HTTP timeout — обычный generation failure: retry status, `next_attempt_at` и attempt count сохраняются; bounded drain продолжает следующую due работу.
- Durable AI quota reservation остаётся перед каждым фактическим model call, включая shortening retry.

## Studio adapter

- `fetch_recent_comments(*, max_cards: int) -> list[Comment]` — единственный scan method рабочего цикла; production вызывает `max_cards=100`.
- Если Studio открыта, обновляется та же вкладка; если закрыта, текущая авторизованная browser page открывает Studio без второй вкладки.
- Метод раскрывает доступные вложенные ответы, возвращает один snapshot; полный `fetch_comments()` не используется как fallback.
- Scan error записывается/логируется без текстов комментариев и не останавливает очередь. Следующая попытка — по обычному 5-минутному расписанию (A12).

## Оркестратор `run_cycle()`

1. Проверяет текущую authenticated session, вызывает repository reconciliation и получает общий work budget.
2. Drain-ит до `MAX_REPLIES_PER_CYCLE` (default 10, A02), используя `MAX_ACTIVE_WORK_ITEMS` (default 30, A16) для capacity окна: due publication раньше generation; generation берёт context и вызывает модель с quota reservation и timeout. Errors queue retry с cooldown/tail order и не останавливают другие ready jobs.
3. Успешный generation атомарно сохраняется и сразу получает одну publication attempt в этом же work-item budget. Publication failure не вызывает generation повторно; неизвестный submit остаётся terminal `unconfirmed`.
4. Затем выполняется short scan, когда он due (сразу на старте, затем каждые 5 минут; если активное окно пусто — раньше, но не чаще раза в минуту после предыдущей попытки). После admission новые jobs обрабатываются в оставшемся budget того же цикла. Общий счётчик не сбрасывается после scan.
5. После любой scan attempt следующий срок становится через пять минут. Успешный ранний scan тем самым сдвигает следующий плановый scan на пять минут от себя; ошибка scan повторяется через пять минут, очередь продолжает обслуживаться.
6. При исчерпании durable AI quota генерация прекращается до следующего цикла, публикации продолжаются если budget остался.

## Разовое production измерение

Read-only SQL после rollout рассчитывает:

- среднее и медиану между первым `comments.fetched_at` и подтверждённым `replies.published_at`, с периодом и числом наблюдений;
- Reply `generated` с pending publication queue state `queued`, `waiting`, `claimed` или `submitting`;
- число stale completions с явной причиной.

О лимите 30 делать предварительный вывод, пока не накопилось 30 подтверждённых публикаций за период наблюдения не короче 24 часов (A11). Секреты, авторы, тексты, URL и HTML в отчёты не включать.

## Правила проекта и проверки

- Python 3.11, synchronous Playwright, SQLAlchemy/PostgreSQL, FastAPI admin; использовать существующие зависимости из `requirements.txt`, без установки новых пакетов. Если необходимой зависимости нет — сообщить `BLOCKED`, не устанавливать самовольно.
- Не редактировать зоны вне ticket, не затрагивать `.env`, авторизацию браузера, admin UI, Telegram/email, VNC и deployment config кроме release ticket; чужие untracked artifacts не стадировать.
- DB SQL и статусные переходы живут в repository. Новую Alembic migration не добавлять без необходимости, подтверждённой исполнителем и согласованной до реализации.
- Тесты: `python -m pytest -q`; DB integration suite только с `TEST_DATABASE_URL` на отдельной disposable PostgreSQL, никогда production. Для изменённых Python-файлов — scoped `ruff check <files>` если Ruff уже доступен.
- Использовать fake/injected page/provider; тесты не логинятся и не публикуют реальные комментарии. Не записывать комментарии, авторов, URL, токены или HTML в логи.
- Production rollout: сделать timestamped PostgreSQL backup и проверить архив; остановить старый worker до возможных миграций; сохранить volume и rollback assets; после старта проверить commit/build, revision, health/heartbeat, активную очередь и фактические counts по saved replies, свежему окну и stale reasons. Production metrics — read-only.
