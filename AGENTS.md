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
# Dzen Commenter — актуальная карта проекта

Синхронный однопроцессный сервис для комментариев Яндекс Дзена: Playwright читает Студию и публикует ответы, AI создаёт текст, PostgreSQL хранит публикации, комментарии, ответы и durable-очереди. FastAPI/Jinja2 admin-панель показывает ленту и историю, меняет несекретные настройки, управляет аккаунтом и доступом VNC. Версия Python — 3.11.

## Подготовка и запуск

Из корня репозитория в PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

Заполняйте локальный `.env`; команда подготовки не перезаписывает существующий файл. Для полного запуска нужны Docker Compose, PostgreSQL и Chromium. `docker compose up --build` запускает `postgres`, воркер `app` и `admin`; миграции выполняются entrypoint перед запуском бота, панель доступна на 8080. В Compose headless включён по умолчанию; браузерные VNC-порты и PostgreSQL не опубликованы наружу. Изменение `POSTGRES_*` не меняет учётные данные уже созданного volume.

Проверки этой итерации 2026-10-09: `python -m pytest -q` — 597 passed, 51 skipped. Ruff по изменённым рабочим модулям прошёл. Полный `python -m ruff check .` сообщает пять известных baseline-ошибок: `.autopilot/sync.py:26`, `dzen_commenter/orchestrator/loop.py:11`, `tests/admin/test_bot_health.py`, `tests/db/test_repository.py:8`, `tests/dzen/test_dzen_page.py`.

## Устройство и границы

- `main.py` — composition root: создаёт PostgreSQL engine/repository, AI provider, hot-reload runtime config, prompt builder, Playwright session, адаптер Дзена и уведомления. `run_supervised` ловит сбои poll-цикла, восстанавливает Chromium при известных crash-ошибках и пишет `BOT_HEALTH_PATH`: перед циклом и по прогрессу сохраняется свежий `cycle_in_progress`; после цикла записывается итог. `/health/bot` возвращает `in_progress`, пока heartbeat свежий; без новых отметок статус становится `stale`.
- `dzen_commenter/orchestrator/loop.py` — единый poll-поток: проверяет авторизацию, получает комментарии, сохраняет их, пропускает собственные ответы/старые комментарии и учитывает часовой лимит; затем запускает не более одной generation-job и одной publication-job за цикл. Очереди при старте цикла повторно подбирают ожидающие eligible-комментарии.
- Поток обработки: `new → generating → generated → publishing → published`; generation может перейти в `skipped`, `generation_retry` или `generation_error`, публикация — в `publication_retry`, `publication_error` или `publication_unconfirmed`. Текст ответа сохраняется до постановки публикации. Раздельные retry применяются к генерации и публикации сохранённого текста.
- `dzen_commenter/db/models.py`, `db/repository.py`, `db/migrations/` — SQLAlchemy и миграции PostgreSQL. У generation/publication очередей свои записи, lease и claim token: устаревший воркер не завершит job, уже захваченную заново. Старые batch-таблицы, если есть в БД, — исторические; текущий runtime не использует batch API.
- `dzen_commenter/dzen/page.py` и `dzen/selectors.py` — Playwright-адаптер Студии и публичной статьи. `fetch_comments()` прокручивает ленту, раскрывает ответы и проверяет повторные снимки; live Playwright извлекает поля видимых карточек и комментариев одним `page.evaluate`, а fake-page сохраняет query-путь. Необязательный progress callback обновляется между проходами и каждые 50 извлечённых комментариев. Если скан не стабилизировался до лимита проходов, выбрасывается `StudioFeedScanIncompleteError`, поэтому частичный результат не возвращается как полный. Карточка без распознанной ссылки пропускается, так как для неё нельзя надёжно построить синтетический id. Не подгоняйте селекторы по одному фрагменту DOM без сценариев на повторный рендер и раскрытие веток.
- Перед submit публикации repository фиксирует marker через `before_submit`. После отправки адаптер проверяет ответ в исходной ветке Студии и на публичной статье; job завершается только при подтверждении. Если отправка могла произойти, но подтверждения нет, результат фиксируется как `publication_unconfirmed`, чтобы не отправлять ответ вслепую повторно. Режим черновика не выставляет `published_at`.
- `dzen_commenter/browser/session_manager.py` владеет persistent Chromium-профилем, storage state и восстановлением. `browser_access()` сериализует работу с браузером через `RLock`. Замену аккаунта выполняйте только штатным `change_account`: этот путь закрывает сессию и удаляет каталог профиля. Входная автоматизация живёт в `auth/dzen_login.py`; Telegram помогает пройти интерактивный код. `auth/dzen_login_control.py` передаёт учётные данные от панели воркеру через локальный Unix socket, не через runtime JSON.
- `dzen_commenter/contracts/` задаёт Protocol-интерфейсы и доменные типы. `ai/factory.py` выбирает провайдера; prompt строят `prompt/builder.py`, `classifier.py` и `config_loader.py`. Ответ модели очищает `contracts/reply_text.py`: только точное `SKIP` означает пропуск, пустой/protocol-only результат считается ошибкой. Новое поведение тестируйте через injected-клиенты и фейки, не открывая реальный Дзен.
- `dzen_commenter/config/runtime_config.py` — несекретный JSON с атомарной записью и перечитыванием по mtime. Admin пишет его, бот читает каждый цикл; при повреждении файла используются последние валидные данные или дефолты. К нему относятся prompt, публикация/лимиты, retry и `bot_account_name`; секреты остаются в окружении.
- `dzen_commenter/admin/` — сессионная FastAPI/Jinja2-панель, запросы к истории и настройкам. Она передаёт смену аккаунта и VNC через socket-клиенты, не управляет Playwright напрямую. `dzen_commenter/vnc_control.py` — отдельный root-owned firewall controller; установочный systemd-файл находится в `deploy/install-vnc-control.sh`.
- `dzen_commenter/monitoring/` — структурные логи, Telegram и настраиваемый SMTP-fallback. Ошибки основного цикла ограничиваются cooldown; Telegram и email для error-уведомлений пытаются доставить независимо.

## Переменные окружения

Ниже только имена; значения задаются локально и не коммитятся. `Settings`/`AdminSettings` читают `.env` и окружение; Compose также использует `POSTGRES_*` и `RUN_DB_MIGRATIONS`.

- БД и тесты: `DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `RUN_DB_MIGRATIONS`, `TEST_DATABASE_URL`.
- AI: `AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY`, `AI_BASE_URL`, `AI_TEMPERATURE`, `AI_MAX_TOKENS`, `AI_PROMPT_LANGUAGE`, `GIGACHAT_AUTH_KEY`, `GIGACHAT_SCOPE`, `GIGACHAT_OAUTH_URL`, `GIGACHAT_BASE_URL`, `GIGACHAT_MODEL`, `GIGACHAT_VERIFY_SSL_CERTS`, `GIGACHAT_CA_BUNDLE`.
- Дзен/браузер/цикл: `USER_DATA_DIR`, `STORAGE_STATE_PATH`, `HEADLESS`, `COMMENTS_URL`, `DZEN_LOGIN_PHONE`, `DZEN_LOGIN_PASSWORD`, `DZEN_LOGIN_TIMEOUT_MS`, `DZEN_LOGIN_CONTROL_SOCKET`, `POLL_INTERVAL`, `KEEPALIVE_INTERVAL`, `MAX_REPLIES_PER_CYCLE`, `BOT_HEALTH_PATH`.
- Уведомления: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PROXY_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`.
- Runtime/admin/VNC: `RUNTIME_CONFIG_PATH`, `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, `VNC_HOST`, `VNC_PORT`, `VNC_PASSWORD`, `NOVNC_PORT`, `VNC_CONTROL_SOCKET`.

## Тесты и подводные камни

Тесты находятся в `tests/`, сгруппированы по `contracts`, `orchestrator`, `db`, `dzen`, `browser`, `auth`, `ai`, `prompt`, `monitoring`, `config` и `admin`. `tests/db/conftest.py` при `TEST_DATABASE_URL` удаляет известные таблицы до/после сессии и очищает строки между тестами; указывайте только отдельную чистую тестовую PostgreSQL. Без переменной DB-интеграционные сценарии пропускаются.

Переходы статусов и очередей проводите атомарными repository-операциями; SQL не переносите в orchestration/UI. Изменения схемы оформляйте миграциями. Не помещайте секреты в runtime JSON, логи или ответы. Не проверяйте изменения реальным логином, браузером или запросами к Дзену: тесты рассчитаны на подменённые клиенты. Перед production-миграциями делайте свежий `pg_dump -Fc` и проверяйте его через `pg_restore --list`; восстановление требует отдельного явного решения.
<!-- autopilot:end -->
