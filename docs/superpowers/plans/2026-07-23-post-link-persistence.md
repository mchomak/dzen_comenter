# Устойчивое сохранение ссылки на пост Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Dzen post links survive DOM format changes, persist in `comments.post_url`, and render as safe admin links.

**Architecture:** Keep the existing data flow and database schema. Relax only the post-link selector so it still targets the link inside the post container while accepting absolute or relative `href` values; keep URL normalization in `DzenStudioPage` and safe URL filtering in `admin.queries`.

**Tech Stack:** Python 3.11, Playwright-style CSS selectors, pytest, SQLAlchemy, FastAPI/Jinja2.

## Global Constraints

- Preserve the raw relative `href` for synthetic comment IDs and reply-node matching.
- Normalize relative Dzen post links to `https://dzen.ru/...` before assigning `Comment.post_url`.
- Admin output allows only HTTPS `dzen.ru`/`www.dzen.ru` URLs with a `/a/...` path.
- Do not add a migration; `comments.post_url` already exists.
- Do not backfill historical `NULL` rows automatically.
- Do not touch the unrelated existing `.gitignore` change.

---

### Task 1: Add a regression test for absolute post hrefs

**Files:**
- Modify: `tests/dzen/test_dzen_page.py:74-79, 250-264`

**Interfaces:**
- Consumes: `DzenStudioPage.fetch_comments()` and `selectors.POST_LINK`.
- Produces: A failing regression test proving an absolute post `href` must be retained and normalized.

- [ ] **Step 1: Make the fake group respect the current selector restriction**

In `FakeGroup.query_selector`, return no post link when the selector still contains
`href^="/a/"` but the fixture uses an absolute URL. Keep the existing relative
fixture behaviour unchanged:

```python
    def query_selector(self, selector: str):
        if selector == selectors.POST_LINK:
            if 'href^="/a/"' in selector and not self._post_link._href.startswith("/a/"):
                return None
            return self._post_link
        if selector == selectors.POST_TITLE:
            return self._title
        return None
```

- [ ] **Step 2: Add the failing absolute-href test**

Add this test beside `test_fetch_comments_sets_post_url_per_group`:

```python
def test_fetch_comments_accepts_absolute_post_href():
    page = DzenStudioPage(
        FakePage([FakeGroup("https://dzen.ru/a/absolute-post", [make_node(0)])])
    )

    assert page.fetch_comments()[0].post_url == "https://dzen.ru/a/absolute-post"
```

- [ ] **Step 3: Run the focused test and verify it fails before the fix**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\dzen\test_dzen_page.py::test_fetch_comments_accepts_absolute_post_href -q
```

Expected: FAIL because the current selector requires `href` to begin with `/a/`,
so `post_link` is not found and `post_url` becomes an empty string.

- [ ] **Step 4: Commit the failing test only**

```powershell
git add tests\dzen\test_dzen_page.py
git commit -m "test: reproduce missing absolute Dzen post link"
```

### Task 2: Relax the post-link selector

**Files:**
- Modify: `dzen_commenter/dzen/selectors.py:211-216`

**Interfaces:**
- Consumes: The existing Dzen Studio group markup.
- Produces: `selectors.POST_LINK` that targets an anchor inside the post container regardless of whether its `href` is relative or absolute.

- [ ] **Step 1: Replace only the href-prefix restriction**

Change:

```python
POST_LINK = '[class*="editor--comments-page__postContainer-"] a[href^="/a/"]'
```

to:

```python
POST_LINK = '[class*="editor--comments-page__postContainer-"] a[href]'
```

Do not change `synthetic_id`, `_parent_comment_id`, or `_iter_comment_nodes`; they
must continue receiving the raw `post_href`.

- [ ] **Step 2: Run the regression test and verify it passes**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\dzen\test_dzen_page.py::test_fetch_comments_accepts_absolute_post_href -q
```

Expected: PASS.

- [ ] **Step 3: Run all Dzen and persistence/admin tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\dzen tests\db\test_repository.py tests\admin\test_comments.py -q
```

Expected: all selected tests pass; the repository tests confirm `post_url` is
stored and updated, and admin tests confirm safe links render.

- [ ] **Step 4: Inspect the diff for scope**

Run:

```powershell
git diff HEAD~2..HEAD --stat
git diff HEAD~2..HEAD --check
```

Expected: only the test and selector changes are present in the implementation
commits; no migration or unrelated formatting changes appear.

- [ ] **Step 5: Commit the implementation**

```powershell
git add dzen_commenter\dzen\selectors.py tests\dzen\test_dzen_page.py
git commit -m "fix: persist absolute Dzen post links"
```

### Task 3: Verify the complete project

**Files:**
- Read-only: all project tests and the final implementation diff.

**Interfaces:**
- Consumes: The committed selector change and existing persistence/admin path.
- Produces: A verified result suitable for handoff.

- [ ] **Step 1: Run the complete test suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS, or report any pre-existing/environment-specific failures with
their exact output.

- [ ] **Step 2: Confirm the final data flow statically**

Verify these existing lines remain present:

```text
dzen_commenter/dzen/page.py: Comment(post_url=_post_url(post_href))
dzen_commenter/db/repository.py: post_url=comment.post_url
dzen_commenter/admin/queries.py: post_url=_post_url(row.post_url)
```

- [ ] **Step 3: Report the result**

Include the root cause, changed files, test command, and whether the complete
suite passed. Mention that historical `NULL` rows are not backfilled by this
change.

