# Admin Panel Usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist comment-thread context, correct Dzen post links, and deliver a responsive admin interface for comments and settings.

**Architecture:** The server-rendered FastAPI/Jinja app receives a nullable history column. The crawler saves an absolute public URL while retaining the raw relative path for its existing synthetic ID. The feed maps stored data to display-ready values and templates/CSS provide the responsive UI.

**Tech Stack:** Python 3.11, SQLAlchemy, Alembic, FastAPI, Jinja2, CSS, pytest.

## Global Constraints

- Keep synthetic IDs based on the original relative `post_href`.
- Existing `thread_text IS NULL` renders `История до комментария не сохранена`.
- No frontend framework or new dependency.
- Links are `https://dzen.ru/...`, open in a new tab, with `rel="noopener noreferrer"`.
- Labels: `published`/«Отправлен», `generated`/«Сгенерирован», `error`/«Ошибка», `skipped`/«Пропущен», none/«Нет ответа».

---

### Task 1: Persist thread history and absolute post URLs

**Files:**
- Modify: `dzen_commenter/db/models.py`
- Modify: `dzen_commenter/db/repository.py`
- Modify: `dzen_commenter/dzen/page.py`
- Create: `dzen_commenter/db/migrations/versions/0003_add_comment_thread_text.py`
- Test: `tests/dzen/test_dzen_page.py`, `tests/db/test_repository.py`

**Interfaces:**
- Consumes: `Comment.thread_text: str` and relative DOM `post_href`.
- Produces: `CommentTable.thread_text: str | None` and externally usable `Comment.post_url`.

- [ ] **Step 1: Write parser regression tests**

```python
assert [c.thread_text for c in comments] == ["", "author0: text0", ""]
assert [c.post_url for c in comments] == [
    "https://dzen.ru/a/post1",
    "https://dzen.ru/a/post1",
    "https://dzen.ru/a/post2",
]
assert comment.dzen_comment_id == synthetic_id("/a/postX", "/user/u5", "text5")
```

- [ ] **Step 2: Run test to prove it fails**

Run: `python -m pytest tests/dzen/test_dzen_page.py -q`

Expected: FAIL because `post_url` is relative.

- [ ] **Step 3: Implement minimal scraper change**

Add a helper that retains full `http`/ `https` URLs and otherwise returns `f"https://dzen.ru{post_href}"`. Pass it only to `Comment(post_url=...)`; retain raw `post_href` for `synthetic_id`, parent lookup, and node matching.

- [ ] **Step 4: Add storage and migration**

Add `thread_text: Mapped[str | None] = mapped_column(Text)` beside `post_url`. Include it in comment insert and conflict-update values. Create:

```python
def upgrade() -> None:
    op.add_column("comments", sa.Column("thread_text", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("comments", "thread_text")
```

- [ ] **Step 5: Test and verify**

Upsert a comment with `"anna: first"`, then `"anna: first\nbob: second"`; assert the latter persists.

Run: `python -m pytest tests/dzen/test_dzen_page.py tests/db/test_repository.py -q`

Expected: PASS.

### Task 2: Render the useful comments feed

**Files:**
- Modify: `dzen_commenter/admin/queries.py`
- Modify: `dzen_commenter/admin/templates/comments.html`
- Test: `tests/admin/test_comments.py`

**Interfaces:**
- Consumes: comment history, URL, and nullable latest reply.
- Produces: `FeedRow.thread_text`, normalised display URL, semantic feed markup.

- [ ] **Step 1: Write failing feed tests**

Extend the test row helper to accept `thread_text=None`. Assert a stored `/a/legacy` maps to `https://dzen.ru/a/legacy`, saved history maps verbatim, and null history stays null. Rendered HTML must contain the fallback phrase, `author: message`, absolute URL, `target="_blank"`, and `rel="noopener noreferrer"`.

- [ ] **Step 2: Run focused test to prove failure**

Run: `python -m pytest tests/admin/test_comments.py -q`

Expected: FAIL because the feed has neither history nor legacy URL handling.

- [ ] **Step 3: Implement query and template**

Select and map `thread_text`; normalise non-empty stored URLs but keep `None` history. Render `.thread-history` for present history or the exact muted fallback. Render current author/comment in `.current-comment`. Use:

```html
<a class="post-link" href="{{ row.post_url }}" target="_blank" rel="noopener noreferrer">Открыть пост</a>
```

Use explicit status classes: `.status-published`, `.status-generated`, `.status-error`, `.status-skipped`, and `.status-missing`; retain error reason.

- [ ] **Step 4: Verify focused test**

Run: `python -m pytest tests/admin/test_comments.py -q`

Expected: PASS.

### Task 3: Reshape shared UI and settings

**Files:**
- Modify: `dzen_commenter/admin/templates/base.html`
- Modify: `dzen_commenter/admin/templates/settings.html`
- Modify: `dzen_commenter/admin/static/style.css`
- Test: `tests/admin/test_auth.py`, `tests/admin/test_settings.py`

**Interfaces:**
- Consumes: request path and existing settings field names.
- Produces: responsive hierarchy without changing POST contracts.

- [ ] **Step 1: Write failing structure tests**

Assert settings HTML contains `settings-layout`, `settings-column`, `settings-bot`, `settings-vnc`, and `settings-prompt`; assert all form names remain. Assert base markup contains `nav-link` and active-path handling.

- [ ] **Step 2: Run tests to prove failure**

Run: `python -m pytest tests/admin/test_auth.py tests/admin/test_settings.py -q`

Expected: FAIL because the new structural classes do not exist.

- [ ] **Step 3: Implement minimal structure**

Place bot and VNC fieldsets in left `.settings-column`, prompt in right. Put submit after `.settings-layout`. Make checkbox a dedicated inline label. Preserve all names, values, readonly state, errors, and validation behaviour. Append `is-active` to the matching nav link.

- [ ] **Step 4: Implement the style system**

```css
.settings-layout {
  display: grid;
  grid-template-columns: minmax(0, .85fr) minmax(0, 1.15fr);
  gap: 1.5rem;
}
@media (max-width: 900px) {
  .settings-layout { grid-template-columns: 1fr; }
}
```

Use a dark-blue sidebar, neutral workspace, white cards, and quiet blue actions. Set prompt textarea `min-height: 10rem`; constrain dialogue/reply cells, add `overflow-wrap: anywhere`, horizontal table scrolling, and green/yellow/red/gray status palettes.

- [ ] **Step 5: Verify admin pages**

Run: `python -m pytest tests/admin/test_auth.py tests/admin/test_settings.py tests/admin/test_comments.py -q`

Expected: PASS.

### Task 4: Verify, stage, review, and deploy

**Files:**
- Modify after independent PASS only: `Projects/Work/dzen-comenter/notes/14-admin-panel-usability.md`.

- [ ] **Step 1: Run complete tests**

Run: `python -m pytest -q`

Expected: no failures; record the computed passed/skipped totals.

- [ ] **Step 2: Inspect the change set**

Run: `git diff --check` and `git diff --stat HEAD`

Expected: no whitespace errors; only stage-14 changes and its migration.

- [ ] **Step 3: Create the stage commit**

```bash
git add dzen_commenter tests
git commit -m "stage-14: improve admin panel usability"
```

- [ ] **Step 4: Independent tester gate**

Dispatch a tester with `Projects/Work/dzen-comenter/notes/14-admin-panel-usability.md` and the commit hash. Do not change code or check off the stage until `VERDICT: PASS`.

- [ ] **Step 5: Push and deploy after PASS**

Push `main`. In `/opt/dzen_comenter`, fast-forward pull and run:

```sh
docker compose up --build -d app admin
docker compose ps app admin
curl --fail --silent http://127.0.0.1:8080/health
curl --fail --silent http://127.0.0.1:8080/comments >/dev/null
docker compose logs --tail=50 app admin
```

Expected: both services run; health is `{"status":"ok"}`; comments responds 200 authenticated or 302 fresh; no fresh migration/runtime errors in logs.
