# Спецификация: отключение batch и прозрачный single-reply flow

## Задача

Batch-генерация добавила редкие multi-item вызовы, отдельную очередь и text-протокол, из-за чего диагностировать генерацию и публикацию стало трудно, а служебные слова стали попадать в видимый ответ. Боту нужен один надёжный путь обработки каждого комментария с раздельными повторными попытками для AI и браузерной публикации.

## Решение

Каждый eligible-комментарий получает ровно одну durable generation job. В том же цикле job получает AI-outcome: `reply` создаёт сохранённый reply и publication job, `skip` терминально пропускает комментарий, ошибка generation планирует новую generation attempt. Publication job пробует сохранённый текст сразу; её ошибка никогда не вызывает новый AI-вызов.

Batch execution path, toggle и batch-настройки удаляются из runtime/admin contract. Существующие batch-таблицы, batch-история и публикационная очередь не удаляются. Новая generation queue независима от batch-таблиц и умеет подхватить незавершённые `new` комментарии без повторного DOM-сбора.

## Пользовательские истории

| # | Метка | История | Приёмка |
|---|---|---|---|
| 1 | R01 | Как владелец бота, я не могу случайно включить batch-обработку, чтобы каждый AI-вызов относился к одному комментарию. | Runtime settings не содержат batch toggle/параметров; `run_cycle` не вызывает batch methods; migration не удаляет исторические batch-записи. |
| 2 | R02 | Как автор комментария, я получаю ответ через последовательность «собрали → сгенерировали → опубликовали». | Один новый комментарий создаёт одну generation job, `reply` создаёт один reply и один publication job, а publication cycle запускается в том же `run_cycle`. |
| 3 | R03.1 | Как оператор, я вижу создание generation job сразу после сохранения комментария. | В одной DB-транзакции сохраняются комментарий и idempotent generation job; повторный scrape не дублирует job. |
| 4 | R03.2, R09 | Как оператор, я вижу точную стадию и последнюю ошибку, а не ложный `skipped`. | Comment status: `new`, `generating`, `generated`, `publishing`, `published`, `skipped`, `generation_retry`, `generation_error`, `publication_retry`, `publication_error`; queue хранит attempt count, next attempt, last error и claimed time. |
| 5 | R04, R05 | Как оператор, я могу настроить retry generation, чтобы временный AI-сбой повторил только генерацию. | Валидируемые live `generation_retry_cooldown_minutes` и `generation_max_attempts_per_comment` управляют scheduling; следующая attempt делает новый вызов AI. |
| 6 | R06, R07 | Как оператор, я не трачу AI-токены повторно из-за DOM/browser-сбоя. | При publication failure reply остаётся `generated`, переиспользуется его сохранённый text и применяется только `publication_retry_*`; после exhaustion фиксируется `publication_error`. |
| 7 | R08 | Как оператор, я отличаю бизнес-пропуск от технической ошибки. | AI outcome `skip` делает comment/reply `skipped`, сохраняет reason и не создаёт generation/publication retry. |
| 8 | R11 | Как читатель Дзена, я не вижу в опубликованном тексте protocol-слов. | AI возвращает только готовый текст или ровно `SKIP`; parser отделяет этот единственный control outcome, sanitizes legacy labels and передаёт браузеру только publishable text. Пустой/некорректный ответ является generation error, не публикуется как текст. |
| 9 | R10 | Как владелец, я вижу фактическую разницу старого и текущего prompt. | Отчёт сравнивает Git baseline `5eab5ff` и current instruction на той же pre-batch выборке стабильности из `reports/stability-history-analysis-2026-09-17.md` (140,51 часа, 761 reply, 0 ReplyError), приводит найденную причину marker leakage и описывает финальный output contract. |
| 10 | R12 | Как владелец, я сохраняю экономию token на чистом article context. | Single generation использует существующий cleaned/cached article context; regression подтверждает, что batch removal не обходит article sanitation. |
| 11 | R13 | Как владелец, я получаю воспроизводимую поставку. | Один commit содержит production code, migration, tests, prompt comparison и ранее созданные исследования; не включает чужие незавершённые `.autopilot` или dashboard edits. |
| 12 | R14, R15 | Как владелец, я получаю проверенный production rollout. | Перед deploy: fresh `pg_dump -Fc` и `pg_restore --list`; после deploy: compose health, migration/startup logs, runtime config without batch settings, and a read-only DB smoke query. |

## Решения по реализации

### Очередь generation

Новая таблица `reply_generation_queue` владеет только повторной генерацией: один active row на comment, `queued|claimed|completed`, `attempt_count`, `next_attempt_at`, `last_error`, `claimed_at`, timestamps. Claim использует `FOR UPDATE SKIP LOCKED` и возвращает один oldest ready comment. Это даёт durable retry без batch grouping и не меняет исторические `comment_batch_queue`, `reply_batches` или `reply_batch_items`.

Queue создаётся idempotently при сохранении eligible comments. Кроме live scrape, orchestration enqueues eligible `new` comments without terminal reply, поэтому накопленные до deploy batch-pending комментарии не остаются бесконечно в старой очереди. Один comment не имеет одновременно generation retry и active publication retry.

### Конечный автомат статусов

```
new → generating → generated → publishing → published
                 ↘ skipped
generating → generation_retry → generating
generation_retry → generation_error
publishing → publication_retry → publishing
publication_retry → publication_error
```

Status update и технический queue transition выполняются одной repository-операцией. `skipped` только outcome модели или существующие business eligibility checks; DOM/AI/DB exception никогда не становится `skipped`. Terminal `generation_error` и `publication_error` сохраняют конкретную phase и last error.

Existing durable `reply_publication_queue` остаётся владельцем повторной публикации. При переходе `generated` transaction одновременно создаёт publication job; это гарантирует, что после restart готовый reply не генерируется повторно. Generation и publication claim возвращают opaque claim token; complete/fail/skip переходы условно обновляют строку только при совпадении token, чтобы просроченный worker не завершил lease, занятый новым worker.

### Runtime/admin configuration

Удаляются batch toggle, cutover, size/wait и batch retry fields. Добавляются и валидируются in-place без restart:

- `generation_retry_cooldown_minutes`: 1–1440, default 60;
- `generation_max_attempts_per_comment`: 1–10, default 3;
- existing `publication_retry_cooldown_minutes` и `publication_max_attempts_per_reply` остаются с теми же границами.

Admin page объясняет, что generation retry повторяет AI, а publication retry использует уже созданный ответ. Runtime config терпимо игнорирует legacy batch JSON keys, чтобы старый файл не ломал worker, но не возвращает их в UI/active settings.

### Prompt и article context

Prompt сохраняет текущий cleaned article-context pipeline из `DzenPage.fetch_article_text` и publication cache. Markdown report сравнивает SHA `5eab5ff`, batch-era prompt и final single prompt на той же pre-batch выборке стабильности из исторического исследования (window 140,51 часа, 761 reply, 0 ReplyError), а не на несопоставимом новом наборе комментариев.

Model output uses the pre-batch single-comment contract: only ready-to-publish text or the exact control word `SKIP`. The parser treats only explicit `SKIP` as a business skip, removes recognised legacy leading labels (`Ответ:`, `Тип ответа:`) before publication, and rejects an empty result or protocol-only result as generation error. This prevents malformed output from becoming either a false `skipped` or public text such as `Ответ`/`SKIP`.

## Границы и швы

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| `orchestrator` | порядок single-comment workflow и quota | one generation cycle + one publication cycle per poll | batch grouping and parser path |
| `repository` | atomic queue/status transitions | enqueue/claim/complete/fail generation; existing publication methods | SQL locks, queue row layout |
| `prompt` / `ai` | JSON outcome contract and prompt wording | parse one model response into `BatchOutcome`-equivalent single outcome | model-specific raw text |
| `runtime/admin` | live retry policy | validated settings read/write | legacy batch keys |
| `dzen.page` | cleaned article extraction and browser publication | existing article/publish operations | DOM scrolling/selectors |

Тестовые швы — existing `CommentRepository`, injected AI provider и Dzen page fake from orchestrator tests. No real Dzen session is used in tests.

## Вне рамок

| Требование | Почему не сейчас |
|---|---|
| — | Нет отложенных требований. |

## Открытые места

Нет placeholders: все технические решения закрыты assumptions полного автомата.

## Покрытие манифеста

| Требование | Раздел спецификации |
|---|---|
| R01 | История 1; Очередь generation; Runtime/admin configuration |
| R02 | История 2; Конечный автомат статусов |
| R03 | Истории 3–4; Конечный автомат статусов |
| R04 | История 5; Runtime/admin configuration |
| R05 | История 5; Очередь generation |
| R06 | История 6; Конечный автомат статусов |
| R07 | История 6; Runtime/admin configuration |
| R08 | История 7; Prompt и article context |
| R09 | История 4; Конечный автомат статусов |
| R10 | История 9; Prompt и article context |
| R11 | История 8; Prompt и article context |
| R12 | История 10; Prompt и article context |
| R13 | История 11 |
| R14 | История 12 |
| R15 | История 12 |
