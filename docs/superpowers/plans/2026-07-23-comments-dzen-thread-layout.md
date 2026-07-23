# Comments Dzen Thread Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace roomy dialogue cards with a compact, Dzen-inspired nested comment tree in the comments table.

**Architecture:** Keep the existing chronological `thread_text` data. Jinja assigns stable depth classes to history, current comment, and bot reply; CSS draws thin connectors and compact indents. The post block always displays either a safe link or a visible fallback.

**Tech Stack:** FastAPI, Jinja2, CSS, pytest.

## Global Constraints

- Preserve five table columns, filters, status rendering, and safe-Dzen-URL policy.
- Do not add avatars, likes, dislikes, reply controls, JavaScript, or dependencies.
- Missing or unsafe URLs render exactly `Ссылка на пост недоступна`.
- The table must be narrower than stage-19's `980px` / `54%` layout and every header must be centred.

---

### Task 1: Render a nested post-centred thread

**Files:**
- Modify: `dzen_commenter/admin/templates/comments.html`
- Test: `tests/admin/test_comments.py`

**Interfaces:**
- Consumes: the existing `FeedRow` data and `thread_messages` filter.
- Produces: `.thread-post`, `.thread-tree`, `.thread-node`, `.thread-current`, and `.thread-bot-reply` hooks.

- [ ] **Step 1: Write failing rendering tests**

```python
body = client.get("/comments").text
assert 'class="thread-tree"' in body
assert 'class="thread-node thread-depth-0"' in body
assert 'Ссылка на пост недоступна' in body
assert 'class="thread-message"' not in body
assert 'Ответить' not in body
```

- [ ] **Step 2: Run focused test**

Run: `python -m pytest tests/admin/test_comments.py -q`
Expected: FAIL because stage-19 emits cards and has no URL placeholder.

- [ ] **Step 3: Implement compact Jinja structure**

```html
<div class="thread-post">
  {% if row.post_url %}<a href="{{ row.post_url }}" target="_blank" rel="noopener noreferrer">{{ row.post_title or "Открыть пост" }}</a>
  {% else %}<span>Ссылка на пост недоступна</span>{% endif %}
</div>
<div class="thread-tree">
  <div class="thread-node thread-depth-0">…</div>
  <div class="thread-current thread-depth-2">…</div>
  <div class="thread-bot-reply thread-depth-3">…</div>
</div>
```

- [ ] **Step 4: Verify template tests**

Run: `python -m pytest tests/admin/test_comments.py -q`
Expected: PASS.

### Task 2: Compact the table and draw the tree

**Files:**
- Modify: `dzen_commenter/admin/static/style.css`
- Test: `tests/admin/test_comments.py`

**Interfaces:**
- Consumes: Task 1 classes.
- Produces: a dense connector-based hierarchy without card borders.

- [ ] **Step 1: Write failing CSS contract tests**

```python
css = Path("dzen_commenter/admin/static/style.css").read_text(encoding="utf-8")
assert "min-width: 760px" in css
assert "table.feed th:nth-child(2) { width: 42%; }" in css
assert ".thread-depth-1 { margin-left: 16px; }" in css
assert ".thread-depth-2 { margin-left: 32px; }" in css
assert ".thread-node {" in css and "border: 0;" in css
```

- [ ] **Step 2: Run focused test**

Run: `python -m pytest tests/admin/test_comments.py -q`
Expected: FAIL because stage-19 retains the 980px / 54% card layout.

- [ ] **Step 3: Implement dense styles**

```css
table.feed { min-width: 760px; }
table.feed th:nth-child(2) { width: 42%; }
.thread-tree { display: grid; gap: 4px; border-left: 1px solid var(--line); }
.thread-node, .thread-current, .thread-bot-reply { position: relative; padding: 2px 0; border: 0; background: transparent; }
.thread-depth-1 { margin-left: 16px; }
.thread-depth-2 { margin-left: 32px; }
.thread-depth-3 { margin-left: 48px; }
```

Centre headers and reduce table-cell padding; remove stage-19 card backgrounds and borders.

- [ ] **Step 4: Verify layout tests**

Run: `python -m pytest tests/admin/test_comments.py -q`
Expected: PASS.

### Task 3: Verify and commit

**Files:**
- Modify: only Task 1–2 files and their tests.

- [ ] **Step 1: Run complete verification**

Run: `python -m pytest -q && git diff --check`
Expected: all tests pass and no whitespace errors.

- [ ] **Step 2: Commit stage**

Run: `git add dzen_commenter/admin/templates/comments.html dzen_commenter/admin/static/style.css tests/admin/test_comments.py; git commit -m "stage-20: render compact dzen comment threads"`
Expected: one coherent commit for independent testing.
