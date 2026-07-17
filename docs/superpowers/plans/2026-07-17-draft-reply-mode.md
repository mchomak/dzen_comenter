# Draft Reply Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AUTO_PUBLISH=false` fill a Dzen reply draft, wait five seconds, and never submit it.

**Architecture:** Extend the existing Dzen page contract with an explicit `auto_publish` argument. The orchestrator remains responsible for reply status and provides `Settings.AUTO_PUBLISH`; the page owns browser actions and timing.

**Tech Stack:** Python 3.12, Playwright sync API, pytest.

## Global Constraints

- Do not add a new environment variable.
- With `AUTO_PUBLISH=false`, no code path may click `REPLY_SUBMIT`.
- The browser pause is exactly `5_000` milliseconds.

---

### Task 1: Draft-aware page interaction

**Files:**
- Modify: `tests/dzen/test_dzen_page.py`
- Modify: `dzen_commenter/contracts/interfaces.py`
- Modify: `dzen_commenter/dzen/page.py`

**Interfaces:**
- Consumes: `DzenPage.publish_reply(comment: Comment, text: str, *, auto_publish: bool)`.
- Produces: a filled reply draft when `auto_publish=False`, or a submitted reply when true.

- [ ] **Step 1: Write the failing test**

```python
def test_publish_reply_fills_draft_and_waits_without_submitting():
    page.publish_reply(target, "мой ответ", auto_publish=False)
    assert node.reply_input.filled == ["мой ответ"]
    assert node.reply_submit.clicks == 0
    assert fake.waited_ms == [5_000]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/dzen/test_dzen_page.py::test_publish_reply_fills_draft_and_waits_without_submitting -v`

Expected: FAIL because `publish_reply` has no `auto_publish` parameter.

- [ ] **Step 3: Write minimal implementation**

```python
def publish_reply(self, comment, text, *, auto_publish):
    # find node, click Reply, fill text
    if auto_publish:
        submit.click()
    else:
        self._page.wait_for_timeout(5_000)
```

- [ ] **Step 4: Run page tests to verify they pass**

Run: `pytest tests/dzen/test_dzen_page.py -v`

Expected: PASS.

### Task 2: Orchestrator safe-mode action

**Files:**
- Modify: `tests/orchestrator/test_loop.py`
- Modify: `tests/orchestrator/conftest.py`
- Modify: `dzen_commenter/orchestrator/loop.py`

**Interfaces:**
- Consumes: `settings.AUTO_PUBLISH` and the page method from Task 1.
- Produces: `ReplyStatus.GENERATED` after draft entry, `ReplyStatus.PUBLISHED` only after successful publishing.

- [ ] **Step 1: Write the failing test**

```python
def test_run_cycle_enters_draft_when_auto_publish_disabled(loop_factory, comment_factory):
    harness = loop_factory(comments=[comment_factory(1)])
    harness.loop.run_cycle()
    assert harness.page.publish_calls == [(harness.repository.comments[1], "generated", False)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/orchestrator/test_loop.py::test_run_cycle_enters_draft_when_auto_publish_disabled -v`

Expected: FAIL because safe mode does not call the page.

- [ ] **Step 3: Write minimal implementation**

```python
self.page.publish_reply(comment, text, auto_publish=self.settings.AUTO_PUBLISH)
if self.settings.AUTO_PUBLISH:
    self.repository.set_reply_status(reply_id, ReplyStatus.PUBLISHED)
```

- [ ] **Step 4: Run orchestrator tests to verify they pass**

Run: `pytest tests/orchestrator/test_loop.py -v`

Expected: PASS.

- [ ] **Step 5: Run the complete suite**

Run: `pytest -q`

Expected: PASS with no failures.
