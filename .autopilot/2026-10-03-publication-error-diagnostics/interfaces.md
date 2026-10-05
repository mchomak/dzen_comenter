# Interfaces and ownership

## Границы, решённые в спецификации

- `DzenStudioPage` in `dzen_commenter/dzen/page.py` owns events for Studio feed/comment reads, article text extraction, reply submission and public reply verification. Existing methods remain the interface; no public API, contract, or model change is needed.
- Dzen failures use existing stdlib logging and stable JSON events. Structured publication logs must keep curated safe error details because `StructuredFormatter` suppresses raw publication tracebacks.
- Fake browser/page tests use the seams in `tests/dzen/test_dzen_page.py`; formatter assertions use `StructuredFormatter` in `dzen_commenter/monitoring/logging_config.py`.
- `docker/entrypoint.sh` owns conditional Alembic startup. `RUN_DB_MIGRATIONS` defaults to the current behavior; exact `false` skips migration. `docker-compose.yml` passes the value to `app`.
- Entrypoint tests use a fake `alembic` executable in `tests/test_entrypoint.py`; Compose environment wiring is checked without connecting to PostgreSQL.
- Comment intake calls the existing `CommentRepository.has_published_reply(comment_id) -> bool` after the atomic `upsert_eligible_comment` seam. The lookup stops generation only when a successful published reply exists; no protocol, repository, model, or migration change is needed.
- `DzenStudioPage` owns retry preflight. Its existing match remains source comment + configured bot author + normalized reply text; the pre-submit search window is 10 seconds, matching the final public verification window.

## Project rules for implementers

- Python 3.11. Focused tests: `.venv\Scripts\python.exe -m pytest -q tests/dzen/test_dzen_page.py tests/test_entrypoint.py tests/test_admin_app_import.py`.
- Full suite: `.venv\Scripts\python.exe -m pytest -q`. Docker/Compose is needed for image verification.
- Do not access real Dzen pages or authenticate a browser. Tests use fake repository/page objects and never connect to PostgreSQL. Do not change PostgreSQL data handling, models, repository, migrations, or the PostgreSQL service; using the existing read API during normal worker intake is explicitly requested. Do not install dependencies automatically.
- The repo has pre-existing user modifications. Stage/commit only files owned by the active ticket and its new tests; never revert or include unrelated work.
- Every stage commit is atomic and names its stage. The independent tester is read-only and must return PASS before the stage is complete.

## Ticket 01 committed interface

- Commit `68ab168670f37c5ed458611493313b252cf47987` keeps public Python signatures unchanged.
- Structured Dzen diagnostics use `failure_stage`, `failure_reason`, `failure_type`, and `failure_description`, with applicable safe counters and HTTP status.
- Do not log the synthetic comment ID or user text/identity/credentials/full URL.

## Ticket 01 repair contract

- Studio source-search and scrolling exceptions emit `publication_source_comment_search_failed` at the lookup boundary and are re-raised unchanged.
- Search completion includes existing `reply_id` when available; `_find_comment_node_with_scroll(comment_id, *, reply_id=None)` remains an internal helper.

## Ticket 01 sanitized exception detail

- Unexpected source-lookup exceptions retain useful sanitized text in `failure_description`; unsafe/empty detail falls back to generic text. The original exception is re-raised unchanged.

## Ticket 03 committed interface

- Intake calls `has_published_reply(comment_id)` after the existing atomic save/enqueue boundary and skips generation when a successfully published reply already exists.
- Retry preflight searches for the matching public reply for up to 10 seconds; each missing-root and DOM-read-error path is capped at 20 waits of 500 ms.
- Commits: `60111af7c2cd182c0032c41698415030a543938e` and repair `8b42a79`; public interfaces and database schema are unchanged.
