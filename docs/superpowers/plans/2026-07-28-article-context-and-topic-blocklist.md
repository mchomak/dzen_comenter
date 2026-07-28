# Article Context and Topic Blocklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate Dzen replies using extracted article text when available, visibly mark fallback replies, and skip replies to the requested restricted topics.

**Architecture:** `DzenStudioPage` reads a post in a short-lived sibling tab and returns text or `None`, always closing that tab. The orchestrator supplies that text and URL to `PromptContext`; the reply persists a separate article-context marker which the admin feed renders without changing the publication lifecycle status.

**Tech Stack:** Python 3, synchronous Playwright, SQLAlchemy/Alembic, FastAPI/Jinja, pytest.

## Global Constraints

- Create every article reader tab from the already authenticated browser context and call `close()` in `finally` on success and failure.
- Cache text or the failed (`None`) result per URL in the `DzenStudioPage` instance; never cache a tab/page.
- No AI-provider browsing or durable storage of an article body.
- Fall back to existing title/thread/comment context if the article cannot be read, and label the saved reply `without_article_text`.
- Preserve `ReplyStatus` semantics; `article_context_status` is a separate value.
- Update only `anti_rules`; leave role, tone, and task prompt fields untouched.
- Use `тип: пропуск` and an empty `ответ` for the nine restricted topics.
- Commit the completed implementation as `stage-22: add article context and topic blocklist`.

---

## File structure

- `dzen_commenter/contracts/interfaces.py` — add URL/text fields to prompt input and the Dzen article-text protocol method.
- `dzen_commenter/contracts/models.py` — add the reply article-context marker.
- `dzen_commenter/dzen/page.py` — open, read, cache, and close a temporary article tab.
- `dzen_commenter/orchestrator/loop.py` — obtain article context, build the enriched prompt, and persist the marker.
- `dzen_commenter/prompt/builder.py` — add the article URL/text block and explicit read-before-answer instruction.
- `dzen_commenter/prompt/config_loader.py`, `config/runtime_config.json`, `prompt_config.example.json` — extend only `anti_rules` with the requested topic blocklist.
- `dzen_commenter/db/models.py`, `dzen_commenter/db/repository.py`, `dzen_commenter/db/migrations/versions/0006_add_reply_article_context_status.py` — persist the marker while leaving old rows as `NULL`.
- `dzen_commenter/admin/queries.py`, `dzen_commenter/admin/templates/comments.html` — load and display article-context information in the feed.
- `tests/dzen/test_dzen_page.py`, `tests/prompt/test_builder.py`, `tests/orchestrator/test_loop.py`, `tests/db/test_repository.py`, `tests/admin/test_comments.py` — regression coverage for each boundary.

### Task 1: Read article text without retaining tabs

**Files:**
- Modify: `dzen_commenter/contracts/interfaces.py`
- Modify: `dzen_commenter/dzen/page.py`
- Modify: `tests/dzen/test_dzen_page.py`

**Interfaces:**
- Produces: `DzenPage.fetch_article_text(post_url: str) -> str | None`.
- Produces: `DzenStudioPage.fetch_article_text(post_url: str) -> str | None`.
- Consumed by: `OrchestratorLoop` in Task 3.

- [x] **Step 1: Write failing tests for extract, cache, and close-on-error behavior**

Add fake browser-context and temporary-page doubles. The temporary page must record `goto`, `close`, and allow a fixture article element. Add these tests:

```python
def test_fetch_article_text_uses_article_body_and_closes_temporary_tab():
    browser = FakePage([FakeGroup("/a/post", [])])
    browser.context.article_pages = [FakeArticlePage(article_text="Полный текст статьи")]
    page = DzenStudioPage(browser)

    assert page.fetch_article_text("https://dzen.ru/a/post") == "Полный текст статьи"
    article_page = browser.context.article_pages[0]
    assert article_page.goto_calls == [("https://dzen.ru/a/post", "domcontentloaded")]
    assert article_page.close_calls == 1


def test_fetch_article_text_closes_failed_temporary_tab_and_caches_none():
    browser = FakePage([FakeGroup("/a/post", [])])
    failed = FakeArticlePage(goto_error=RuntimeError("unavailable"))
    browser.context.article_pages = [failed]
    page = DzenStudioPage(browser)

    assert page.fetch_article_text("https://dzen.ru/a/post") is None
    assert page.fetch_article_text("https://dzen.ru/a/post") is None
    assert failed.close_calls == 1
    assert browser.context.new_page_calls == 1
```

- [x] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/dzen/test_dzen_page.py -k "fetch_article_text" -v`

Expected: FAIL because the protocol/implementation method and test doubles do not exist.

- [x] **Step 3: Implement the minimal article reader**

Add the protocol signature and initialize a per-instance cache:

```python
class DzenPage(Protocol):
    def fetch_article_text(self, post_url: str) -> str | None: ...


class DzenStudioPage:
    def __init__(self, page) -> None:
        self._page = page
        self._article_text_by_url: dict[str, str | None] = {}
```

Implement `fetch_article_text` with these rules:

```python
if not post_url:
    return None
if post_url in self._article_text_by_url:
    return self._article_text_by_url[post_url]

article_page = self._page.context.new_page()
try:
    article_page.goto(post_url, wait_until="domcontentloaded")
    article = article_page.query_selector("article") or article_page.query_selector("main")
    text = article.inner_text().strip() if article else ""
except Exception:
    text = ""
finally:
    article_page.close()
self._article_text_by_url[post_url] = text or None
return self._article_text_by_url[post_url]
```

Do not alter comment extraction or reply publication.

- [x] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/dzen/test_dzen_page.py -k "fetch_article_text or implements_dzen_page_contract" -v`

Expected: PASS.

### Task 2: Enrich prompts and restrict the requested topics

**Files:**
- Modify: `dzen_commenter/contracts/interfaces.py`
- Modify: `dzen_commenter/prompt/builder.py`
- Modify: `dzen_commenter/prompt/config_loader.py`
- Modify: `config/runtime_config.json`
- Modify: `prompt_config.example.json`
- Modify: `tests/prompt/test_builder.py`

**Interfaces:**
- Consumes: `PromptContext(publication_title, thread_text, reply_type, comment_text, post_url, article_text)`.
- Produces: a prompt article block only when `article_text` is non-empty.
- Consumed by: `OrchestratorLoop` in Task 3.

- [x] **Step 1: Write failing prompt tests**

Add tests that assert the builder carries the URL and exact supplied article text with an explicit reading instruction, and that all three active/default configuration sources have the nine-topic restriction and skip rule:

```python
def test_article_text_context_instructs_model_to_read_article_before_reply():
    result = DameoPromptBuilder().build(
        PromptContext(
            publication_title="article",
            thread_text="thread",
            comment_text="comment",
            reply_type="engage",
            post_url="https://dzen.ru/a/post",
            article_text="Подробности статьи",
        )
    )
    assert "https://dzen.ru/a/post" in result
    assert "Подробности статьи" in result
    assert "Перед ответом внимательно прочитай текст статьи" in result


def test_default_anti_rules_skip_every_requested_restricted_topic():
    for topic in (
        "политика", "власть", "политические деятели", "секс", "наркотики",
        "медицинские препараты", "государственные органы", "зарплаты", "пенсии",
    ):
        assert topic in DEFAULT_ANTI_RULES.lower()
    assert "тип: пропуск" in DEFAULT_ANTI_RULES.lower()
    assert "пуст" in DEFAULT_ANTI_RULES.lower()
```

For the JSON files, parse their UTF-8 text with `json.loads` and apply the same topic assertions to `payload["prompt"]["anti_rules"]` and `payload["anti_rules"]` respectively.

- [x] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/prompt/test_builder.py -k "article_text_context or restricted_topic" -v`

Expected: FAIL because `PromptContext` has no article fields and the topic blocklist is absent.

- [x] **Step 3: Implement the prompt-only changes**

Append optional fields to `PromptContext` to retain current call sites:

```python
post_url: str | None = None
article_text: str = ""
```

In `DameoPromptBuilder.build`, add this block only when `context.article_text` is non-empty:

```python
article_block = (
    "КОНТЕКСТ СТАТЬИ:\n"
    f"Ссылка на статью: {context.post_url or 'недоступна'}\n"
    "Перед ответом внимательно прочитай текст статьи и отвечай с опорой на него.\n"
    f"Текст статьи:\n{context.article_text}"
)
```

Insert `article_block` after the existing input/context block and before the task. Do not alter the role, tone, task lead, or task engage strings.

Extend only each `anti_rules` value with a bullet that names all nine topics and directs the model to output `тип: пропуск` with an empty `ответ`. Preserve all pre-existing bullets, including the current answer-length rule in the live runtime configuration.

- [x] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/prompt/test_builder.py -v`

Expected: PASS.

### Task 3: Persist article-context status through reply generation

**Files:**
- Modify: `dzen_commenter/contracts/models.py`
- Modify: `dzen_commenter/orchestrator/loop.py`
- Modify: `dzen_commenter/db/models.py`
- Modify: `dzen_commenter/db/repository.py`
- Create: `dzen_commenter/db/migrations/versions/0006_add_reply_article_context_status.py`
- Modify: `tests/orchestrator/conftest.py`
- Modify: `tests/orchestrator/test_loop.py`
- Modify: `tests/db/test_repository.py`

**Interfaces:**
- Consumes: `DzenPage.fetch_article_text(post_url) -> str | None` and enriched `PromptContext`.
- Produces: `Reply.article_context_status: str | None`, set to `article_text_used` or `without_article_text` for every newly generated reply.
- Consumed by: admin feed in Task 4.

- [x] **Step 1: Write failing loop and repository tests**

Make `FakeDzenPage` expose `article_text_by_url: dict[str, str | None]` and record lookup URLs. Add these loop tests:

```python
def test_run_cycle_passes_article_text_and_marks_reply_as_article_text_used(loop_factory, comment_factory):
    comment = comment_factory(1)
    comment.post_url = "https://dzen.ru/a/post"
    harness = loop_factory(comments=[comment])
    harness.page.article_text_by_url[comment.post_url] = "Article body"

    harness.loop.run_cycle()

    assert harness.prompt_builder.contexts[0].article_text == "Article body"
    assert harness.prompt_builder.contexts[0].post_url == comment.post_url
    assert next(iter(harness.repository.replies.values())).article_context_status == "article_text_used"


def test_run_cycle_falls_back_and_marks_reply_without_article_text(loop_factory, comment_factory):
    comment = comment_factory(1)
    comment.post_url = "https://dzen.ru/a/post"
    harness = loop_factory(comments=[comment])
    harness.page.article_text_by_url[comment.post_url] = None

    harness.loop.run_cycle()

    assert harness.prompt_builder.contexts[0].article_text == ""
    assert next(iter(harness.repository.replies.values())).article_context_status == "without_article_text"
```

Add a repository assertion querying `replies.article_context_status` after `save_reply`.

- [x] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/orchestrator/test_loop.py -k "article_text" -v; pytest tests/db/test_repository.py -k "article_context_status" -v`

Expected: FAIL because neither the protocol fake, reply field, nor persistence column exists.

- [x] **Step 3: Implement the minimal data flow and migration**

Add the optional dataclass field after `created_at`:

```python
article_context_status: str | None = None
```

In `_generate_reply`, fetch article text once before `PromptContext` construction:

```python
article_text = self.page.fetch_article_text(comment.post_url) if comment.post_url else None
article_context_status = "article_text_used" if article_text else "without_article_text"
```

Pass `post_url=comment.post_url` and `article_text=article_text or ""` into `PromptContext`. Extend `_make_reply` to accept and set `article_context_status`, and pass the same local marker for generated and error replies created in this path.

Add `ReplyTable.article_context_status: Mapped[str | None] = mapped_column(Text)` and include it in `save_reply` values. Create migration revision `0006_add_reply_article_context_status`, with `down_revision = "0005_store_moscow_time"`, that adds a nullable `Text` column to `replies`; downgrade drops it. This preserves one linear Alembic head after stage-21's existing `0005_store_moscow_time` migration.

- [x] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/orchestrator/test_loop.py -k "article_text" -v; pytest tests/db/test_repository.py -k "article_context_status" -v`

Expected: PASS.

### Task 4: Show the saved context state in the admin feed and verify the stage

**Files:**
- Modify: `dzen_commenter/admin/queries.py`
- Modify: `dzen_commenter/admin/templates/comments.html`
- Modify: `tests/admin/test_comments.py`

**Interfaces:**
- Consumes: `ReplyTable.article_context_status` from Task 3.
- Produces: `FeedRow.article_context_status: str | None` and an admin label.

- [x] **Step 1: Write failing admin tests**

Extend the reply fixture helper to accept `article_context_status`. Add a query test and HTML test:

```python
def test_feed_exposes_article_context_status(engine):
    _add_comment(engine, cid=1, author="alice", text="hi", post_url="/a/p", fetched_at=datetime.now())
    _add_reply(engine, comment_id=1, status="generated", article_context_status="without_article_text")

    assert fetch_feed(engine)[0].article_context_status == "without_article_text"


def test_comments_page_shows_article_context_status(client, engine):
    _add_comment(engine, cid=1, author="alice", text="hi", post_url="/a/p", fetched_at=datetime.now())
    _add_reply(engine, comment_id=1, status="generated", article_context_status="article_text_used")

    assert "Учтён текст статьи" in client.get("/comments").text
```

Also assert a historical reply with `None` renders «Нет данных».

- [x] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/admin/test_comments.py -k "article_context_status" -v`

Expected: FAIL because the feed query and template do not expose the marker.

- [x] **Step 3: Implement the feed mapping and labels**

Add `article_context_status: str | None` to `FeedRow`, select `ReplyTable.article_context_status` with the existing latest reply columns, and map it from the latest reply. In the existing reply-status table cell, render a separate `status-badge` after the lifecycle badge:

```jinja2
{% if row.article_context_status == "article_text_used" %}
  <span class="status-badge">Учтён текст статьи</span>
{% elif row.article_context_status == "without_article_text" %}
  <span class="status-badge">Сгенерирован без текста статьи</span>
{% elif row.reply_status is not none %}
  <span class="status-badge">Нет данных</span>
{% endif %}
```

Do not add an admin filter and do not change existing lifecycle labels.

- [x] **Step 4: Run focused tests, then the full suite**

Run: `pytest tests/admin/test_comments.py -k "article_context_status" -v`

Expected: PASS.

Run: `pytest -q`

Expected: PASS with zero failures.

- [x] **Step 5: Review and commit only stage-22 changes**

Run: `git diff --check; git status --short; git diff -- dzen_commenter tests config prompt_config.example.json`

Confirm the diff contains only the files listed in this plan, then commit the implementation and tests:

```bash
git add dzen_commenter tests config/runtime_config.json prompt_config.example.json
git commit -m "stage-22: add article context and topic blocklist"
```

## Plan self-review

- Spec coverage: Task 1 implements temporary-tab extraction, cache, and guaranteed closure; Task 2 adds article prompt context and only changes anti-rules; Task 3 delivers fallback markers and migration; Task 4 exposes them in the admin feed and runs the full suite.
- No placeholders: all tests, interfaces, migration values, fallback behaviour, and commit scope are explicit.
- Type consistency: `fetch_article_text(str) -> str | None`, `PromptContext.post_url`, `PromptContext.article_text`, and `Reply.article_context_status` use the same names across tasks.
