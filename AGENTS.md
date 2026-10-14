# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

# PROJECT WORKFLOW & CONVENTIONS

This project runs as a three-role pipeline. This file is the shared contract:
it is loaded into every agent (orchestrator, coder, tester), so the conventions
below apply to all of them. Role-specific behavior lives in each agent's own
definition file; the rules here are the common ground.

## 5. Roles

- **Orchestrator** — the main session (Opus). Plans, owns all Obsidian notes,
  delegates work, decides what to do on failures. The ONLY role that edits notes.
- **coder** — subagent (Sonnet). Implements exactly one stage from its stage note,
  then commits. Never edits notes.
- **tester** — subagent (Sonnet). Read-only. Verifies the last commit against the
  stage note and returns a PASS/FAIL verdict. Never edits code or notes.

Hard rules for all roles:
- Subagents cannot spawn other subagents. coder and tester report back to the
  orchestrator only via their final message.
- On an ambiguous, contradictory, or underspecified plan: STOP. Do not improvise.
  coder/tester surface the problem to the orchestrator; the orchestrator re-reads
  context and resolves it (see §1).
- **A stage is "done" ONLY after tester returns PASS.** Nothing else marks completion.

## 6. Obsidian Vault Conventions (via the Obsidian MCP server)

The Obsidian vault is the single source of truth and the only reliable channel
between orchestrator and subagents (a subagent starts with fresh context and sees
only the prompt it's handed). The orchestrator passes a stage-note path; coder and
tester open and read that note themselves before doing anything.

The vault has its OWN authoritative `CLAUDE.md` at its root with the full
conventions (complete tag taxonomy, folder→frontmatter table, templates, move
rules). The agents run from the CODE repo and reach the vault only over MCP, so
the vault's `CLAUDE.md` is NOT auto-loaded into their context — the rules the
pipeline depends on are reproduced below. If anything about vault mechanics is
unclear, read the vault's own `CLAUDE.md` over MCP first; it wins on conflicts.

### Where pipeline notes live
Freelance/client projects go under `Projects/Work/<Project>/`; personal projects
under `Projects/Personal/<Project>/` (same layout, omit `client`).

**Note layout — root vs wave-folders.** The project root holds only durable,
*current* notes: the main project note, the live architecture/tech doc(s), decision
notes, and key references. Stage/implementation notes are grouped into **wave
subfolders** — one folder per development wave or redesign (e.g. SkillUp has
`pipeline-v1-frequency-core/`, `mvp-gamification/`, `redesign-v2-topdown/`). This
keeps the root a clean snapshot of the system as it is now, while superseded
build-logs stay preserved but out of the way. A new stage note goes into the
**current** wave's folder.

**MCP mechanics for filing a stage note:** `create_note` cannot target a subfolder —
it always lands the note flat in the project root. So filing is two steps:
`create_note` (root) → `move_note` into the current wave folder. Moving only changes
the folder, so `[[wiki-links]]` are untouched (Obsidian resolves them by filename).
There is no `create_folder` over MCP: if the current wave folder doesn't exist yet,
ask the human to create it, then move into it. Don't let stage notes pile up in the
root.

- **Main project note** — title `<project-slug>`, `type: project`
  - frontmatter: `type: project`, `project_status`, `client: "[[…]]"` (Work only),
    >=1 Тематика tag
  - body: purpose, overall architecture, key decisions, data model, and
    `[[links]]` to every stage note
- **Stage notes** — title `NN-stage-slug` (e.g. `01-foundation`), `type: note`
  - frontmatter: `type: note`, `project: "[[<project-slug>]]"`, >=1 Тематика tag
  - body: stage goal · architecture for the stage · surface-level logic ·
    concrete details (what/how to use, available data, interfaces) ·
    **Acceptance criteria (testable)** · implementation checklist
- **Decision notes** — title `decision-<slug>`, `type: decision`
  - Logged whenever the orchestrator resolves a FAIL or makes a non-trivial
    architectural call, so the reasoning survives across sessions.
  - frontmatter: `type: decision`, `decision_status: proposed|accepted|rejected`,
    `date`, `project: "[[…]]"`, >=1 Тематика tag
  - any numbers in a decision (e.g. a contrast target) must be code-computed (§10),
    never estimated
- Prefer the matching `note_type` template if the vault applies one.

**Acceptance criteria** are explicit, binary, testable statements — e.g.
"button text contrast >= 4.5:1 (AA normal), computed via code", "invalid email is
rejected before submit", "yearly toggle recomputes all three prices". They are
exactly what the tester checks, and they are what lets delegation prompts stay
thin (§11). A vague criterion ("looks good") is a bug in the note — fix the note.

### Vault mechanics the agents MUST follow
- **Links**: always `[[Note name without extension]]` (or `[[Note|display text]]`).
  Never markdown `[text](path.md)` — Obsidian won't show it in the graph.
- **Filenames**: cyrillic/latin, digits, spaces, hyphens only. Forbidden chars:
  `: / \ * ? " < > | # ^ [ ]`. So note titles use hyphens/spaces and NEVER a colon
  (e.g. `02-auth-flow`, not `stage-02: auth`). The git commit message in §8 may use
  a colon; that restriction is for filenames only.
- **Tags**: only from the vault's approved list, in YAML `tags:`. The Тематика
  category — required for project/note/decision notes — is: `ml`, `ai`,
  `education`, `dev-tools`, `prompt-engineering`, `obsidian`, `social`, `youtube`,
  `gamedev`, `dataset`, `object-detection`, `startup`, `web`.
- **Never touch `.obsidian/`** — don't read or write it.
- **Never hard-delete** a note. Soft-move to `Archive/<original-folder>/` and add
  `archived_at: <ISO date>`.
- Before mass edits (>10 notes), show the plan first.

### Checklist semantics (inside stage notes)
- `- [ ]` = pending
- `- [x]` = done — set by the orchestrator ONLY after tester returns PASS

### Note discipline
- Only the orchestrator writes/edits notes. coder and tester are read-only on notes.
- Keep heavy detail in the notes, not in chat. This keeps the orchestrator's context
  clean and lets any fresh subagent reconstruct full context from the note alone.
- Each stage note must be self-contained enough that coder/tester need nothing beyond
  it plus the codebase to do their job.

## 7. Stage Loop (run once per stage)

Delegate with the thin contract in §11 — point coder/tester at the note, don't
re-paste it. Delegations are idempotent: coder commits are atomic per stage and the
tester is read-only, so any delegation can be safely re-run if interrupted (e.g. a
usage limit) without corrupting state.

1. Orchestrator delegates to **coder** with the stage-note path.
   → coder implements ONLY that stage (§2, §3, §9), then commits (§8).
2. Orchestrator delegates to **tester** with the same stage-note path and the commit hash.
   → tester diffs that commit against the note's acceptance criteria and returns PASS or FAIL.
3. **PASS** → orchestrator checks off completed items in the stage note, then moves
   to the next stage.
4. **FAIL** → orchestrator re-reads the project context and the stage note, identifies
   the root cause, fixes/clarifies the note if the plan was at fault, and logs any
   non-trivial architectural decision as a decision note (§6; numbers in it computed
   via code, §10). Then re-delegates to coder (thin re-pass prompt, §11), then to
   tester again. Repeat until PASS — or, if blocked by genuine ambiguity, stop and
   ask the human.

## 8. Commits

- coder commits per stage with a clear message that names the stage
  (e.g. `stage-02: <slug> — <what>`), so tester can reliably review "the last commit."
- One stage = one coherent commit (or a small, related set). Don't bundle multiple
  stages into one commit; tester verifies against a single stage note.

## 9. Scope Discipline (reinforces §2 and §3 for the pipeline)

- coder implements only the current stage. No future-stage work, no speculative
  abstractions, no touching code outside what the stage requires.
- coder must NOT knowingly commit code that violates the stage note's acceptance
  criteria. If a criterion cannot be met within the stage's scope, coder STOPS and
  reports to the orchestrator (with code-computed numbers where relevant) instead of
  committing a known-failing result. A known violation is not a "deviation note" — it's
  a stop.
- tester reports issues precisely (file/line, expected vs actual, computed numbers).
  It never fixes code and never edits notes — fixing is the next coder pass, decided
  by the orchestrator.

## 10. Numeric & Factual Verification (all roles)

Any numeric or factual claim that can be computed or checked MUST be produced by
running code — never estimated from intuition. This covers contrast ratios, font
sizes, percentages, timings, element counts, and similar. Always state the computed
value next to the threshold/expected value it is compared against.

- The **coder** computes (e.g. python via Bash) before claiming a number; it never
  writes a figure it didn't compute.
- The **tester** recomputes independently and NEVER trusts numbers reported by the
  coder or written in commit messages — if the coder says "3.6:1", the tester
  derives its own value and judges against that.
- The **orchestrator** computes (or has the tester compute) any number it puts into
  a decision note.

(Rationale: a single contrast value was once hand-estimated four different ways
across the roles; only the code-computed value was correct.)

## 11. Thin Delegation Contract

The stage note already carries the full plan and acceptance criteria, so the
orchestrator must NOT copy that content into delegation prompts. Re-pasting bloats
the expensive (Opus) context and duplicates the source of truth. Delegate with the
minimum the subagent can't derive itself:

- **To coder:** `Implement stage <NN> per its note: <note-path>. Repo: <repo-path>.`
  On a re-pass after FAIL, add ONLY: the specific failed criterion, a one-line
  pointer to the tester's finding, and the decision-note path if one exists.
- **To tester:** `Verify commit <hash> against stage <NN> note: <note-path>. Repo: <repo-path>.`
  Nothing else — the tester reads the acceptance criteria itself and uses its own
  response format.

Add a line of genuinely out-of-note context only if needed (e.g. an environment
quirk). Never paste the note's checklist into the prompt.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer
rewrites due to overcomplication, clarifying questions come before implementation
rather than after mistakes, every stage in Obsidian is checked off only after a real
PASS, every number that gates a PASS was computed by code (not estimated), and 

<!-- autopilot:start -->
# Dzen Commenter — карта текущего состояния

Python 3.11+, синхронный Playwright, FastAPI/Jinja2 и PostgreSQL. `docker-compose.yml` запускает `app` (worker), `admin` и PostgreSQL 16; наружу Compose публикует только `8080:8080`. Именованные тома хранят БД, Chromium profile/session и runtime config. `docker/entrypoint.sh` запускает Alembic до приложения, если `RUN_DB_MIGRATIONS` не равно точному `false`; при `HEADLESS=false` entrypoint поднимает Xvfb, x11vnc и noVNC. VNC доступ управляется отдельным host-side socket controller.

## Команды

PowerShell, из корня репозитория; локальные Python-команды требуют настроенного Python 3.11 и зависимостей проекта. Перед запуском приложения или Compose заполните `.env`:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m pytest -q
python -m pytest -q tests/dzen/test_dzen_page.py
python -m pytest -q tests/orchestrator/test_loop.py tests/orchestrator/test_generation_queue.py
python main.py
python -m uvicorn dzen_commenter.admin.app:app --host 0.0.0.0 --port 8080
docker compose up --build
```

Последняя сообщённая проверка в основной checkout: полный набор — `647 passed, 64 skipped`, одно известное предупреждение Starlette/httpx deprecation при unset `TEST_DATABASE_URL`; `tests/dzen/test_dzen_page.py` — `170 passed`. DB-интеграционные тесты пропускаются без `TEST_DATABASE_URL`.

## Структура и ключевые файлы

- `main.py` собирает БД, AI provider, live runtime config, prompt builder, browser session, Studio adapter, notifier и account-login socket; `run_supervised` ведёт worker, heartbeat/watchdog, ограниченные повторы и восстановление Chromium после renderer crash.
- `dzen_commenter/orchestrator/loop.py` задаёт порядок цикла и обработки очередей. `dzen_commenter/db/repository.py` — транзакционные SQL-переходы, admission, leases/retries и quota; модели/transport-типы — в `dzen_commenter/db/models.py` и `dzen_commenter/contracts/`. Текущий migration head — `dzen_commenter/db/migrations/versions/0012_ai_call_reservations.py`.
- `dzen_commenter/dzen/page.py` — адаптер Studio/public comments и публикации; `dzen_commenter/browser/session_manager.py` владеет persistent Playwright session и восстановлением браузера. Селекторы находятся в `dzen_commenter/dzen/selectors.py`.
- `dzen_commenter/config/settings.py` — process environment; `dzen_commenter/config/runtime_config.py` — hot-reload JSON. `dzen_commenter/ai/factory.py` выбирает OpenAI-compatible, GigaChat или YandexGPT adapter; YandexGPT пока заглушка. `dzen_commenter/prompt/` собирает брендовый prompt и выбирает reply type/CTA candidate.
- `dzen_commenter/admin/app.py`, `dzen_commenter/admin/queries.py`, `dzen_commenter/admin/validation.py` — защищённая панель, лента/история и запись настроек. Смена аккаунта идёт через `dzen_commenter/auth/dzen_login_control.py`; VNC — через `dzen_commenter/admin/vnc_access.py` и host controller `dzen_commenter/vnc_control.py`.
- `dzen_commenter/bot_health.py` пишет атомарный snapshot worker heartbeat/status; `dzen_commenter/monitoring/` содержит JSON logging, Telegram и SMTP fallback. Не логировать тексты комментариев, авторов, URL, HTML и секреты.

## Обработка комментария и границы очередей

1. `run_cycle()` проверяет сессию, вызывает `reconcile_work_window()` с лимитом `MAX_ACTIVE_WORK_ITEMS`, затем использует общий лимит `MAX_REPLIES_PER_CYCLE`. Repository ограничивает активную работу уникальными comment IDs в `reply_generation_queue` и `reply_publication_queue`, переводит лишние queued rows в `waiting` и в reconciliation завершает просроченную pending work по `posted_at` (fallback `fetched_at`); переходы очередей и Comment/Reply фиксируются транзакционно.
2. Сначала берётся due publication job, затем generation. Успешная генерация сохраняет Reply и publication row; первая попытка публикации относится к тому же work item. Publication retry использует сохранённый текст и не запускает генерацию повторно. Claims используют `FOR UPDATE SKIP LOCKED` и claim token; устаревший worker не может завершить job, уже взятый новым claim.
3. Перед каждым фактическим вызовом AI repository атомарно резервирует слот в скользящем часовом лимите, включая единственный shortening retry. Строки reservations хранятся в `ai_call_reservations`; provider timeout (`AI_REQUEST_TIMEOUT_SECONDS`) для OpenAI-compatible/GigaChat превращается в generation retry. `MAX_ACTIVE_WORK_ITEMS` имеет положительный default 30; `MAX_REPLIES_PER_CYCLE` обязателен в `Settings`.
4. Успешная generation добавляет публикацию в durable очередь. Перед Studio submit сначала сохраняется `submitting`; если submit был, но результат нельзя подтвердить, состояние становится `publication_unconfirmed` и автоматически не отправляется повторно. Публичный поиск последовательно раскрывает порции ответов с повторным чтением актуального DOM. Исчерпанный публичный поиск допускает одну отправку через точный найденный Studio source с конечными `publication_unconfirmed`/`unconfirmed`; уже видимая точная Studio reply при отсутствии публичного source завершается теми же статусами без send. До durable submit обычные технические ошибки публикации используют собственные cooldown/attempt limits. См. ADR-0026.
5. После drain выполняется `fetch_recent_comments(max_cards=100)`: первый short scan после старта, затем обычно каждые пять минут; пустое окно допускает более раннюю проверку через минуту. Admission фильтрует настроенный bot account, собственные ответы и слишком старые комментарии, сортирует новые по времени и помещает в свободные слоты. При наличии остатка budget добавленные jobs обрабатываются в том же цикле. Ошибка scan логируется и не отменяет уже обслуженную очередь.
6. Worker cycle не вызывает полный `fetch_comments()` как fallback: отсутствие `fetch_recent_comments` завершает scan ошибкой. Live Studio scan сверяет DOM snapshot со счётчиками. В каждом short intake и Studio source lookup раскрытие ответов использует единый 60s deadline на операцию, общий для групп и не перезапускаемый на каждую карточку. При timeout short intake извлекает доступные в DOM комментарии, возвращает их и пишет `studio_comments_read_incomplete` с `failure_reason=reply_expansion_time_limit_reached`; следующий scan может забрать раскрытые позже ответы. Source lookup проверяет максимум первые 100 Studio groups и при известном `post_url` раскрывает нужную публикацию; timeout выходит до `before_submit`, поэтому публикационный job уходит в обычный retry без submit. Полный `fetch_comments()` сохраняет 30m cap и распространяет timeout вместо возврата неполного полного списка. Feed passes и expansion продолжают обновлять heartbeat.

Runtime JSON содержит несекретные настройки публикации, возраста/длины ответа, часового AI лимита, CTA, generation/publication retry, bot account, получателей уведомлений и поля prompt. Admin атомарно записывает файл, worker перечитывает его по mtime и сохраняет последние валидные данные при ошибке чтения. Пароли и ключи задаются окружением, не runtime JSON.

## Переменные окружения

Только имена, собранные из `Settings`, `AdminSettings`, Compose, entrypoint и DB test setup:

- PostgreSQL / миграции / тесты: `DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `RUN_DB_MIGRATIONS`, `TEST_DATABASE_URL`.
- AI: `AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY`, `AI_BASE_URL`, `AI_TEMPERATURE`, `AI_MAX_TOKENS`, `AI_PROMPT_LANGUAGE`, `AI_REQUEST_TIMEOUT_SECONDS`, `GIGACHAT_AUTH_KEY`, `GIGACHAT_SCOPE`, `GIGACHAT_OAUTH_URL`, `GIGACHAT_BASE_URL`, `GIGACHAT_MODEL`, `GIGACHAT_VERIFY_SSL_CERTS`, `GIGACHAT_CA_BUNDLE`.
- Дзен / browser / цикл: `USER_DATA_DIR`, `STORAGE_STATE_PATH`, `HEADLESS`, `COMMENTS_URL`, `DZEN_LOGIN_PHONE`, `DZEN_LOGIN_PASSWORD`, `DZEN_LOGIN_TIMEOUT_MS`, `DZEN_LOGIN_CONTROL_SOCKET`, `POLL_INTERVAL`, `KEEPALIVE_INTERVAL`, `MAX_REPLIES_PER_CYCLE`, `MAX_ACTIVE_WORK_ITEMS`, `BOT_HEALTH_PATH`.
- Telegram / SMTP: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PROXY_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`.
- Admin / runtime / VNC: `RUNTIME_CONFIG_PATH`, `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, `VNC_HOST`, `VNC_PORT`, `VNC_PASSWORD`, `VNC_CONTROL_SOCKET`, `NOVNC_PORT`.

## Тесты и реальные подводные камни

- `tests/` разбит на `admin`, `ai`, `auth`, `browser`, `config`, `contracts`, `db`, `dzen`, `monitoring`, `orchestrator`, `prompt` и корневые integration/regression tests. Ключевые очереди: `tests/orchestrator/test_loop.py`, `tests/orchestrator/test_generation_queue.py`, `tests/db/test_repository.py`; Studio: `tests/dzen/test_dzen_page.py` (включает regression cases на возврат видимых комментариев при timeout intake и retry source lookup до submit); схема: `tests/test_migrations.py`.
- `tests/db/conftest.py` удаляет известные таблицы, применяет миграции и очищает таблицы. Указывайте `TEST_DATABASE_URL` только на отдельную disposable PostgreSQL; на обычной БД этот setup разрушителен. Без переменной DB integration tests skip.
- Тесты используют fake/injected page/provider; не проверять изменения реальной авторизацией или публикацией в Дзене и не обращаться к живому Studio.
- Compose требует настроенные PostgreSQL-переменные; смена значений не публикует VNC ports через Compose — admin управляет ими отдельным host socket. `MAX_REPLIES_PER_CYCLE` остаётся обязательной process setting; не путать его с capacity активного окна `MAX_ACTIVE_WORK_ITEMS`.
<!-- autopilot:end -->
