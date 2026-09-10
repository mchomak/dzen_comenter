# Границы, решённые в спецификации

## Проектные правила

- Python 3.11, SQLAlchemy, Alembic, FastAPI; тесты не используют реальный браузер или Дзен.
- Миграция только аддитивна. Не менять существующие данные production.
- Runtime JSON содержит только несекретные live-настройки.
- Перед production-миграцией обязателен проверенный `pg_dump`; restore не запускается без нового явного указания пользователя.

## Швы

| Модуль | Владеет | Выставляет | Скрывает |
|---|---|---|---|
| `CommentRepository` | transaction/locking для generation и publication очередей, article cache | методы Protocol для claim/save/retry | SQLAlchemy rows и raw SQL layout |
| `OrchestratorLoop` | порядок capture, generation, publication и quota | `run_cycle()` с injected dependencies | детали таблиц и Playwright |
| `DzenPage` | DOM-поиск и ввод ответа | `publish_reply()` | CSS selectors и browser timing |
| Runtime config | live retry limits публикации | typed settings | JSON parsing и mtime reload |

## Task 02 — live settings

- `RuntimeSettings.publication_retry_cooldown_minutes: int = 60`
- `RuntimeSettings.publication_max_attempts_per_reply: int = 3`
- The admin accepts and persists only non-secret values: cooldown `1..1440`, attempts `1..10`.

## Task 01 — persistence and migration

- `ArticleContext` stores cached article text and its retrieval state; `ClaimedPublication` represents one claimed publication job.
- `CommentRepository.get_article_context/save_article_context/enqueue_publication/claim_next_publication/complete_publication/fail_publication` are the only persistence seam for cached context and publication retries.
- Migration `0009_add_article_context_and_publication_queue` is additive: nullable publication fields, a new queue table and indexes; it performs no update/delete of existing data.

## Task 03 — DB-backed orchestration

- `OrchestratorLoop` builds batch generation from claimed DB rows, so a comment absent from the current DOM can still be generated after the partial-batch timeout.
- Article context is read from repository cache; the loop fetches and persists it once when absent, with a safe empty-context fallback.
- Valid batch replies enqueue a `ClaimedPublication`; publication is separately claimed and completed/failed. DOM lookup/browser failures retry only the publication job and never regenerate or mark an item skipped.

Тестовые швы: `CommentRepository`, `DzenPage`, `AIProvider`, `BatchPromptBuilder`, `BatchReplyParser` через уже существующие Protocol/fake adapters.
