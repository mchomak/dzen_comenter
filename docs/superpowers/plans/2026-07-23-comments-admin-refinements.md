# Comments Admin Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the comments feed compact, searchable by suggested authors, and readable as a post-centred conversation thread.

**Architecture:** Persist each newly crawled post title in a nullable comment column. The feed maps it with the existing safe URL, provides unique authors for a native datalist, and renders a compact hierarchical Jinja view. Existing rows remain usable through their `Открыть пост` link.

**Tech Stack:** Python 3.11, SQLAlchemy, Alembic, FastAPI, Jinja2, CSS, pytest.

## Global Constraints

- No new frontend dependency or JavaScript framework.
- Preserve the existing case-insensitive substring author query and safe-Dzen-URL policy.
- Do not backfill historical post titles; retain the existing fallback link.
- Display `fetched_at` only to seconds, without changing persistence or ordering.
- All acceptance criteria are in Obsidian stage note `Projects/Work/dzen-comenter/notes/19-comments-admin-refinements.md`.

---

### Task 1: Persist a per-comment post title

**Files:**
- Modify: `dzen_commenter/db/models.py`, `dzen_commenter/db/repository.py`
- Create: `dzen_commenter/db/migrations/versions/0004_add_comment_post_title.py`
- Test: `tests/db/test_repository.py`

**Interfaces:**
- Consumes: existing `Comment.publication_title: str`.
- Produces: nullable `CommentTable.post_title` saved by `PostgresCommentRepository.upsert_comment`.

- [ ] **Step 1: Write failing persistence tests**

```python
comment = _make_comment(pub_id, post_url="http://post", publication_title="Post title")
comment_id = repo.upsert_comment(comment)
assert engine.connect().execute(text("SELECT post_title FROM comments WHERE id = :id"), {"id": comment_id}).scalar_one() == "Post title"
```

- [ ] **Step 2: Run the focused test**

Run: `python -m pytest tests/db/test_repository.py -q`
Expected: FAIL because `post_title` does not exist.

- [ ] **Step 3: Implement storage and migration**

```python
# models.py
post_title: Mapped[str | None] = mapped_column(Text)

# migration upgrade
op.add_column("comments", sa.Column("post_title", sa.Text(), nullable=True))

# repository insert and conflict update
post_title=comment.publication_title or None
```

- [ ] **Step 4: Verify persistence**

Run: `python -m pytest tests/db/test_repository.py -q`
Expected: PASS.

### Task 2: Extend the feed and render the compact thread

**Files:**
- Modify: `dzen_commenter/admin/queries.py`, `dzen_commenter/admin/app.py`, `dzen_commenter/admin/templates/comments.html`, `dzen_commenter/admin/static/style.css`
- Test: `tests/admin/test_comments.py`

**Interfaces:**
- Consumes: `FeedRow.post_title`, safe `FeedRow.post_url`, and `list[str]` author options.
- Produces: `fetch_author_options(engine) -> list[str]`, compact dialogue markup, and second-precision timestamp output.

- [ ] **Step 1: Write failing admin tests**

```python
body = client.get("/comments?q=ali").text
assert 'name="q" list="author-options"' in body
assert '<option value="alice">' in body
assert body.count('<option value="alice">') == 1
assert 'class="thread-post-link"' in body
assert '2026-01-01 12:00:00.123456' not in body
assert '2026-01-01 12:00:00' in body
```

- [ ] **Step 2: Run focused test**

Run: `python -m pytest tests/admin/test_comments.py -q`
Expected: FAIL because the feed has no author options, title link, or time formatting.

- [ ] **Step 3: Implement queries and route context**

```python
def fetch_author_options(engine: Engine, limit: int = 100) -> list[str]:
    return sorted({row.author for row in _load_feed(engine, limit) if row.author}, key=str.casefold)

# app.py template context
{"feed": feed, "authors": fetch_author_options(engine) if engine is not None else [], ...}
```

Extend `FeedRow` and `_load_feed` to select and map `CommentTable.post_title`; keep `_post_url` as the sole URL validator.

- [ ] **Step 4: Implement template and CSS**

```html
<input type="text" name="q" list="author-options" value="{{ q }}">
<datalist id="author-options">
  {% for author in authors %}<option value="{{ author }}">{% endfor %}
</datalist>
{% if row.post_title and row.post_url %}
  <a class="thread-post-link" href="{{ row.post_url }}" target="_blank" rel="noopener noreferrer">{{ row.post_title }}</a>
{% endif %}
<td>{{ row.fetched_at.strftime("%Y-%m-%d %H:%M:%S") if row.fetched_at else "" }}</td>
```

Centre `table.feed thead th`; reduce dialogue message padding/margins; use a left border and indentation for history/current/bot blocks so the visual reading order is post, comment history, current comment, then bot reply.

- [ ] **Step 5: Verify admin behavior**

Run: `python -m pytest tests/admin/test_comments.py -q`
Expected: PASS.

### Task 3: Full verification and stage commit

**Files:**
- Modify: only files needed by Tasks 1–2, the corresponding tests, and the two documentation files.

- [ ] **Step 1: Run complete tests**

Run: `python -m pytest -q`
Expected: PASS with totals recorded from command output.

- [ ] **Step 2: Check diff hygiene**

Run: `git diff --check && git diff --stat`
Expected: no whitespace errors and only stage-19 scope.

- [ ] **Step 3: Commit the stage**

Run:

```bash
git add dzen_commenter tests docs/superpowers
git commit -m "stage-19: refine comments admin feed"
```

Expected: one coherent commit ready for independent verification.
