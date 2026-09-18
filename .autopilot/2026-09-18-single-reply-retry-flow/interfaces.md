# Границы, решённые в спецификации

## Project rules

- Python 3.11; tests: `.venv\Scripts\python.exe -m pytest -q`.
- New logic must use existing injected `CommentRepository`, `AIProvider` and `DzenPage` seams. Tests never invoke real Dzen/Playwright.
- Preserve the existing article cleanup/cache and durable publication queue. Do not delete production records or batch-era DB tables.
- A generation retry repeats only the AI request; a publication retry uses saved reply text only.
- User requested one shared final commit. Do not commit individual tickets and do not stage unrelated existing edits in `.autopilot/dashboard.html` or `.gitignore`.

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| `contracts` | single generation types and repository methods | claim/complete/fail generation operations | persistence layout |
| `db` | atomic generation queue and status state machine | idempotent enqueue and one-item claim | locking, timestamps and SQL mappings |
| `runtime/admin` | user-facing retry controls | validated generation/publication retry settings | legacy batch JSON keys |
| `orchestrator` | one-comment generation/publication order | one run-cycle workflow | queue transport and prompt raw output |
| `prompt` | single output contract | ready text / explicit skip parser | label sanitization details |

## Test seams

- Repository fake in `tests/orchestrator/conftest.py` verifies workflow decisions without PostgreSQL.
- PostgreSQL repository tests verify idempotency, locking and state transitions.
- Prompt builder/parser tests verify explicit skip, stripped legacy labels and rejected protocol-only output.
