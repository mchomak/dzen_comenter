# Admin Comments History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing feed the admin home page and deliver a separate full-history comments page with Moscow-time persistence, date filtering, sort order, and reliable Dzen article URLs.

**Architecture:** One parameterized Jinja feed template serves the unchanged, 100-row home feed and the unbounded history route. `fetch_feed` keeps its 100-row default for home while accepting optional date bounds and ordering for history. A small shared clock module supplies naive Moscow wall-clock values for all new persisted timestamps; an Alembic migration shifts existing timestamp values once.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, SQLAlchemy 2, Alembic, PostgreSQL, pytest.

## Global Constraints

- `GET /` retains the current feed's 100-row limit, status/author filters, table, and descending order.
- `GET /comments` has no row limit and supports independent `date_from`, `date_to`, and `order=asc|desc` query parameters.
- Date bounds apply to `comments.fetched_at` in Moscow calendar time: start inclusive at midnight; end inclusive through the day, implemented with the next midnight as an exclusive bound.
- Invalid dates and sort values must safely fall back to no bound and newest-first ordering.
- Existing values in `comments.posted_at`, `comments.fetched_at`, `replies.published_at`, and `replies.created_at` shift by exactly three hours in the forward migration; downgrade reverses the shift.
- New persisted timestamps are timezone-naive Moscow wall-clock values from `ZoneInfo("Europe/Moscow")`.
- Only valid `https://dzen.ru/a/...` or `https://www.dzen.ru/a/...` targets are saved and rendered as article links. Historical empty URLs remain unrecoverable and show `Ссылка отсутствует`.
- Do not add frontend dependencies or change unrelated admin settings behavior.

---

### Task 1: Persist new times in Moscow and migrate historical timestamps

**Files:**
- Create: `dzen_commenter/time_utils.py`
- Create: `dzen_commenter/db/migrations/versions/0005_store_moscow_time.py`
- Modify: `dzen_commenter/dzen/page.py`, `dzen_commenter/orchestrator/loop.py`
- Modify: `tests/dzen/test_dzen_page.py`, `tests/orchestrator/test_loop.py`, `tests/db/test_repository.py`

**Interfaces:**
- Produces `moscow_now() -> datetime`: a timezone-naive `Europe/Moscow` wall-clock time.
- `DzenStudioPage.fetch_comments()` consumes `moscow_now()` for both `fetched_at` and the base time passed to `parse_relative_time`.
- `OrchestratorLoop._make_reply()` consumes `moscow_now()` for `Reply.created_at`.

- [ ] **Step 1: Write failing tests for the shared clock and call sites.**

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from dzen_commenter.time_utils import moscow_now


def test_moscow_now_returns_naive_moscow_wall_clock(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 28, 9, 15, tzinfo=ZoneInfo("UTC")).astimezone(tz)

    monkeypatch.setattr("dzen_commenter.time_utils.datetime", FrozenDatetime)

    assert moscow_now() == datetime(2026, 7, 28, 12, 15)
```

Update the Dzen-page expectation from an aware UTC `fetched_at` to a naive value, and add an orchestrator test that monkeypatches `dzen_commenter.orchestrator.loop.moscow_now` and asserts the saved reply has that exact `created_at` value.

- [ ] **Step 2: Run the focused tests and verify the expected failures.**

Run: `python -m pytest tests/dzen/test_dzen_page.py tests/orchestrator/test_loop.py -q`

Expected: FAIL because `time_utils` and the call-site imports do not exist, while existing code still returns UTC-aware Dzen times and calls `datetime.now()` directly.

- [ ] **Step 3: Implement the minimal shared clock and use it at every new-write source.**

```python
# dzen_commenter/time_utils.py
from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    return datetime.now(MOSCOW).replace(tzinfo=None)
```

Replace `datetime.now(timezone.utc)` in `dzen_commenter/dzen/page.py` with `moscow_now()` and remove the unused `timezone` import. Replace `datetime.now()` in `OrchestratorLoop._make_reply()` with `moscow_now()`; retain `datetime` for `_is_too_old` annotations and comparisons.

- [ ] **Step 4: Write a migration regression test before creating the migration.**

In `tests/db/test_repository.py`, downgrade a clean test database to revision `0004_add_comment_post_title`, insert one comment and one reply with all four timestamp fields set, upgrade to `head`, and assert the retrieved values are respectively the seeded time plus `timedelta(hours=3)`. Restore the schema to `head` before the test exits.

```python
assert row.posted_at == datetime(2026, 7, 28, 15, 0)
assert row.fetched_at == datetime(2026, 7, 28, 15, 1)
assert reply.published_at == datetime(2026, 7, 28, 15, 2)
assert reply.created_at == datetime(2026, 7, 28, 15, 3)
```

- [ ] **Step 5: Run the migration test and verify it fails before the new revision exists.**

Run: `python -m pytest tests/db/test_repository.py -q`

Expected: when `TEST_DATABASE_URL` is configured, FAIL because upgrading from revision `0004_add_comment_post_title` does not shift values. Without the variable, pytest reports this database suite as skipped; record that fact and run the remaining non-Postgres suites.

- [ ] **Step 6: Add the reversible Alembic revision.**

```python
# dzen_commenter/db/migrations/versions/0005_store_moscow_time.py
revision = "0005_store_moscow_time"
down_revision = "0004_add_comment_post_title"


def upgrade() -> None:
    op.execute("UPDATE comments SET posted_at = posted_at + INTERVAL '3 hours', fetched_at = fetched_at + INTERVAL '3 hours'")
    op.execute("UPDATE replies SET published_at = published_at + INTERVAL '3 hours', created_at = created_at + INTERVAL '3 hours'")


def downgrade() -> None:
    op.execute("UPDATE comments SET posted_at = posted_at - INTERVAL '3 hours', fetched_at = fetched_at - INTERVAL '3 hours'")
    op.execute("UPDATE replies SET published_at = published_at - INTERVAL '3 hours', created_at = created_at - INTERVAL '3 hours'")
```

`NULL + interval` remains `NULL`, so no special-case SQL is necessary.

- [ ] **Step 7: Verify focused behavior and commit the task.**

Run: `python -m pytest tests/dzen/test_dzen_page.py tests/orchestrator/test_loop.py tests/db/test_repository.py -q`

Expected: PASS, with the database suite passing when `TEST_DATABASE_URL` is available or being explicitly skipped when it is not.

```bash
git add dzen_commenter/time_utils.py dzen_commenter/dzen/page.py dzen_commenter/orchestrator/loop.py dzen_commenter/db/migrations/versions/0005_store_moscow_time.py tests/dzen/test_dzen_page.py tests/orchestrator/test_loop.py tests/db/test_repository.py
git commit -m "stage-21: store timestamps in Moscow time"
```

### Task 2: Retrieve reliable Dzen article links

**Files:**
- Modify: `dzen_commenter/dzen/selectors.py`, `dzen_commenter/dzen/page.py`, `tests/dzen/test_dzen_page.py`

**Interfaces:**
- Produces `POST_LINK_FALLBACK`, an article-anchor selector usable within a `POST_GROUP`.
- Produces `_post_url(post_href: str) -> str`, which returns an absolute safe Dzen article URL or `""`.
- `DzenStudioPage.fetch_comments()` first queries `POST_LINK`, then `POST_LINK_FALLBACK` only when needed.

- [ ] **Step 1: Write failing tests for fallback selection and URL rejection.**

Extend the fake post group so `POST_LINK` can return `None` while `POST_LINK_FALLBACK` returns a supplied link. Add tests for these exact outcomes:

```python
assert DzenStudioPage(FakePage([fallback_group("/a/fallback", [make_node(0)])])).fetch_comments()[0].post_url == "https://dzen.ru/a/fallback"
assert DzenStudioPage(FakePage([fallback_group("https://dzen.ru/a/absolute", [make_node(0)])])).fetch_comments()[0].post_url == "https://dzen.ru/a/absolute"
assert DzenStudioPage(FakePage([fallback_group("https://evil.example/a/x", [make_node(0)])])).fetch_comments()[0].post_url == ""
```

- [ ] **Step 2: Run the Dzen-page test module and verify the fallback test fails.**

Run: `python -m pytest tests/dzen/test_dzen_page.py -q`

Expected: FAIL because `POST_LINK_FALLBACK` is absent and the current implementation has no fallback query.

- [ ] **Step 3: Add the fallback selector and strict URL normalizer.**

```python
# selectors.py
POST_LINK_FALLBACK = (
    'a[href^="/a/"], '
    'a[href^="https://dzen.ru/a/"], '
    'a[href^="https://www.dzen.ru/a/"]'
)
```

Use `urllib.parse.urlsplit` in `_post_url`. Return `https://dzen.ru` plus a relative `/a/...` path; otherwise accept only `https`, host `dzen.ru` or `www.dzen.ru`, and path beginning `/a/`. Return `""` for every other value. In `fetch_comments`, query `POST_LINK`, then query `POST_LINK_FALLBACK` if the first link is absent or normalizes to `""`; preserve the original `post_href` for `synthetic_id` so comment identity does not change.

- [ ] **Step 4: Verify the focused tests and commit the task.**

Run: `python -m pytest tests/dzen/test_dzen_page.py -q`

Expected: PASS.

```bash
git add dzen_commenter/dzen/selectors.py dzen_commenter/dzen/page.py tests/dzen/test_dzen_page.py
git commit -m "stage-21: recover Dzen article links"
```

### Task 3: Add full history route, date filters, sorting, and navigation

**Files:**
- Modify: `dzen_commenter/admin/app.py`, `dzen_commenter/admin/queries.py`, `dzen_commenter/admin/templates/base.html`, `dzen_commenter/admin/templates/comments.html`, `tests/admin/test_comments.py`
- Delete: none

**Interfaces:**
- `fetch_feed(engine, limit=100, status=None, author_query=None, date_from=None, date_to=None, order="desc") -> list[FeedRow]`; `date_to` is exclusive when provided.
- `GET /` calls `fetch_feed(..., limit=100)` and renders the feed template with `is_history=False`.
- `GET /comments` calls `fetch_feed(..., limit=None)` and renders the feed template with `is_history=True` plus parsed filter values.

- [ ] **Step 1: Write failing query tests for unbounded, date-filtered, ordered history.**

Add five comments at `2026-07-01 23:59:59`, `2026-07-02 00:00:00`, `2026-07-02 12:00:00`, `2026-07-02 23:59:59`, and `2026-07-03 00:00:00`. Assert all of the following:

```python
assert len(fetch_feed(engine, limit=None)) == 105  # fixture has more than the home limit
assert authors(fetch_feed(engine, limit=None, date_from=datetime(2026, 7, 2))) == ["next", "end", "middle", "start"]
assert authors(fetch_feed(engine, limit=None, date_to=datetime(2026, 7, 3))) == ["end", "middle", "start"]
assert authors(fetch_feed(engine, limit=None, date_from=datetime(2026, 7, 2), date_to=datetime(2026, 7, 3))) == ["end", "middle", "start"]
assert authors(fetch_feed(engine, limit=None, order="asc")) == sorted_authors_by_time_ascending
```

- [ ] **Step 2: Run the query tests and verify they fail.**

Run: `python -m pytest tests/admin/test_comments.py -q`

Expected: FAIL because `limit=None`, date bounds, and ascending ordering are unsupported.

- [ ] **Step 3: Implement database-side date predicates and deterministic ordering.**

Build the `select(CommentTable...)` statement in `_load_feed` with:

```python
if date_from is not None:
    statement = statement.where(CommentTable.fetched_at >= date_from)
if date_to is not None:
    statement = statement.where(CommentTable.fetched_at < date_to)
if order == "asc":
    statement = statement.order_by(CommentTable.fetched_at.asc(), CommentTable.id.asc())
else:
    statement = statement.order_by(CommentTable.fetched_at.desc(), CommentTable.id.desc())
if limit is not None:
    statement = statement.limit(limit)
```

Keep the default `limit=100` and `order="desc"` so the home feed's existing behavior is unchanged. Preserve the existing status and author filtering after rows have been assembled.

- [ ] **Step 4: Write failing route and template tests.**

Add tests proving:

```python
assert "Показано 100 из последних 100" in client.get("/").text
assert "Показано 105" in client.get("/comments").text
assert "name=\"date_from\"" in client.get("/comments").text
assert "name=\"date_to\"" in client.get("/comments").text
assert "name=\"order\"" in client.get("/comments").text
assert "middle" in client.get("/comments?date_from=2026-07-02&date_to=2026-07-02").text
assert "middle" in client.get("/comments?date_from=not-a-date&order=bad").text
assert body.index("older") < body.index("newer")  # /comments?order=asc
assert 'href="/"' in body and ">Главная<" in body
assert 'href="/comments"' in body and ">Комментарии<" in body
```

Move existing expectations for the unchanged feed from `/comments` to `/` and replace the former dashboard test with an assertion that `/` renders the feed.

- [ ] **Step 5: Run route tests and verify they fail.**

Run: `python -m pytest tests/admin/test_comments.py -q`

Expected: FAIL because `/` renders the old dashboard and `/comments` does not render date/sort controls or all rows.

- [ ] **Step 6: Implement route parsing and the parameterized feed template.**

In `app.py`, parse valid ISO `date` values with `date.fromisoformat`; turn start into `datetime.combine(value, time.min)` and the end into `datetime.combine(value + timedelta(days=1), time.min)`. Invalid input becomes `None`. Pass `limit=100, is_history=False, action="/"` for home and `limit=None, is_history=True, action="/comments"` for history.

In `comments.html`, use `action` for both forms. Render date inputs and the order select only when `is_history`. Keep the existing current-home controls and layout otherwise. Render `Показано {{ feed | length }}` on history and retain `Показано {{ feed | length }} из последних 100` on home. Preserve all active values in the refresh form.

In `base.html`, add the `Главная` link to `/`, keep `Комментарии` at `/comments`, and set `aria-current` against the exact current path.

- [ ] **Step 7: Verify focused admin tests and commit the task.**

Run: `python -m pytest tests/admin/test_comments.py -q`

Expected: PASS.

```bash
git add dzen_commenter/admin/app.py dzen_commenter/admin/queries.py dzen_commenter/admin/templates/base.html dzen_commenter/admin/templates/comments.html tests/admin/test_comments.py
git commit -m "stage-21: add full admin comment history"
```

### Task 4: Run complete verification

**Files:**
- Modify: only files required to correct a failing test from Tasks 1–3.

**Interfaces:**
- Consumes the completed Moscow time, Dzen link, and admin history behavior.
- Produces a verified stage-21 commit set with no whitespace errors.

- [ ] **Step 1: Run the full test suite.**

Run: `python -m pytest -q`

Expected: all runnable tests PASS; record the exact passed/skipped counts from command output.

- [ ] **Step 2: Validate migration availability and diff hygiene.**

Run: `python -m alembic heads && git diff --check HEAD~3..HEAD && git status --short`

Expected: one Alembic head at `0005_store_moscow_time`, no whitespace errors, and a clean working tree. If Task 1 produced a different number of commits, replace `HEAD~3..HEAD` with the range from immediately before Task 1.

- [ ] **Step 3: Commit only a required verification fix.**

If and only if Steps 1–2 required a code correction, add the smallest relevant files, rerun their focused tests, and commit:

```bash
git commit -m "stage-21: fix verification findings"
```

Otherwise make no additional commit.
