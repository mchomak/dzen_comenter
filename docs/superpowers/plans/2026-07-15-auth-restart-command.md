# Telegram Auth Restart Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/auth` so the configured Telegram chat can discard the current Dzen session and restart the existing manual authorization flow.

**Architecture:** `TelegramAuthAssistant` will non-blockingly consume command updates and retain their offset. `OrchestratorLoop` will invoke it before session restoration; a recognized command resets the Playwright browser state and permits `ask_ready()` to send its existing prompt again.

**Tech Stack:** Python 3.11, httpx, Playwright sync API, pytest.

## Global Constraints

- Accept `/auth` only from the configured Telegram chat.
- Preserve the exact existing ready-prompt copy and «Готов» button.
- Do not add threads, processes, environment variables, dependencies, or unrelated refactors.
- Use TDD: each behavioural test must fail before its production implementation is written.

---

### Task 1: Telegram command polling and prompt reset

**Files:**
- Modify: `dzen_commenter/auth/telegram_auth_assistant.py`
- Modify: `dzen_commenter/contracts/interfaces.py`
- Test: `tests/auth/test_telegram_auth_assistant.py`

**Interfaces:**
- Produces: `TelegramAuthAssistant.poll_auth_command() -> bool`
- Produces: `TelegramAuthAssistant.reset_ready_prompt() -> None`
- Produces: matching methods on `AuthAssistant`.

- [ ] **Step 1: Write failing tests.**

```python
def test_poll_auth_command_returns_true_for_configured_chat():
    update = {"update_id": 10, "message": {"chat": {"id": 12345}, "text": "/auth"}}
    assistant, _ = _assistant(RequestRecorder([_json_response({"ok": True, "result": [update]})]))
    assert assistant.poll_auth_command() is True

def test_reset_ready_prompt_allows_sending_prompt_again():
    assistant, _ = _assistant(recorder)
    assert assistant.ask_ready() is False
    assistant.reset_ready_prompt()
    assert assistant.ask_ready() is False
    assert methods == ["sendMessage", "getUpdates", "sendMessage", "getUpdates"]
```

- [ ] **Step 2: Run the focused tests and verify RED.**

Run: `python -m pytest tests/auth/test_telegram_auth_assistant.py -q`

Expected: FAIL because the two new methods do not exist.

- [ ] **Step 3: Implement minimal command polling.**

```python
def poll_auth_command(self) -> bool:
    for update in self._get_updates(timeout=0):
        if self._matching_auth_command(update):
            return True
    return False

def reset_ready_prompt(self) -> None:
    self._ready_prompt_sent = False
```

Use an instance `_update_offset` for all `getUpdates` calls. `_matching_auth_command` must accept only `/auth` or a single `/auth@botname` token from `self.chat_id`.

- [ ] **Step 4: Run focused tests and verify GREEN.**

Run: `python -m pytest tests/auth/test_telegram_auth_assistant.py -q`

Expected: PASS.

### Task 2: Browser-state reset and orchestration

**Files:**
- Modify: `dzen_commenter/browser/session_manager.py`
- Modify: `dzen_commenter/contracts/interfaces.py`
- Modify: `dzen_commenter/orchestrator/loop.py`
- Test: `tests/browser/test_session_manager.py`
- Test: `tests/orchestrator/test_loop.py`

**Interfaces:**
- Consumes: `AuthAssistant.poll_auth_command() -> bool`, `AuthAssistant.reset_ready_prompt() -> None`.
- Produces: `SessionManager.reset_authentication() -> None`.

- [ ] **Step 1: Write failing tests.**

```python
def test_reset_authentication_clears_cookies_removes_state_and_opens_comments(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    settings = make_settings(STORAGE_STATE_PATH=str(state_path))
    page = FakePage()
    context = FakeContext(page)
    manager = PlaywrightSessionManager(settings, playwright_factory=make_factory(context))
    manager.start()

    manager.reset_authentication()

    assert context.clear_cookies_calls == 1
    assert not state_path.exists()
    assert page.goto_calls[-1] == settings.COMMENTS_URL
```

```python
def test_run_cycle_resets_session_and_restarts_authorization_on_auth_command(
    loop_factory, comment_factory
):
    session = FakeSessionManager(logged_in=False, restore_results=[False])
    auth_assistant = FakeAuthAssistant(
        auth_command_result=True, ask_ready_result=False
    )
    harness = loop_factory(
        comments=[comment_factory(1)], session=session, auth_assistant=auth_assistant
    )

    harness.loop.run_cycle()

    assert session.reset_authentication_calls == 1
    assert auth_assistant.reset_ready_prompt_calls == 1
    assert auth_assistant.ask_ready_calls == 1
```

- [ ] **Step 2: Run focused tests and verify RED.**

Run: `python -m pytest tests/browser/test_session_manager.py tests/orchestrator/test_loop.py -q`

Expected: FAIL because reset methods and command handling do not exist.

- [ ] **Step 3: Implement minimal reset path.**

```python
def reset_authentication(self) -> None:
    if self._context is not None:
        self._context.clear_cookies()
    Path(self._settings.STORAGE_STATE_PATH).unlink(missing_ok=True)
    if self._page is not None:
        self._page.goto(self._settings.COMMENTS_URL)
```

At the start of `run_cycle()`, call `poll_auth_command()`. When it returns true, call `session.reset_authentication()` and `auth_assistant.reset_ready_prompt()` before `_ensure_session()`.

- [ ] **Step 4: Run focused tests and verify GREEN.**

Run: `python -m pytest tests/browser/test_session_manager.py tests/orchestrator/test_loop.py -q`

Expected: PASS.

### Task 3: Regression suite and delivery checks

**Files:**
- Verify only: modified files and all tests.

- [ ] **Step 1: Run full test suite.**

Run: `python -m pytest -q`

Expected: all executable tests pass; database integration tests may remain skipped when `TEST_DATABASE_URL` is absent.

- [ ] **Step 2: Validate the Compose configuration.**

Run: `docker compose config --quiet`

Expected: exit code 0.

- [ ] **Step 3: Commit implementation.**

```bash
git add dzen_commenter/auth/telegram_auth_assistant.py dzen_commenter/browser/session_manager.py dzen_commenter/contracts/interfaces.py dzen_commenter/orchestrator/loop.py tests/auth/test_telegram_auth_assistant.py tests/browser/test_session_manager.py tests/orchestrator/test_loop.py
git commit -m "stage-06: add Telegram auth restart command"
```
