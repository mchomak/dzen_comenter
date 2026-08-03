# Персонализация ответов и нативный CTA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Публиковать персонализированные ответы с именем автора и передавать CTA в ИИ как органичную часть ответа вместо отдельной ссылки.

**Architecture:** Оркестратор нормализует текст ИИ, добавляет префикс автора и учитывает его длину до сохранения и публикации. Текущий отбор CTA-кандидатов сохраняется, но CTA передаётся дополнительной инструкцией в промпт, а не присоединяется к готовому тексту.

**Tech Stack:** Python 3.11, FastAPI/Jinja, pytest.

## Global Constraints

- Итоговый ответ с непустым автором: \`<author>, <ответ ИИ>\`; первая буква ИИ-текста строчная.
- CTA-интервал и определение CTA-кандидата не меняются.
- CTA не присоединяется к ответу программно отдельной строкой.
- \`max_reply_length\` ограничивает итоговый публикуемый текст.
- CTA-поле принимает непустой произвольный текст, в том числе \`domeo ru\`.

---

### Task 1: Префикс автора и CTA-инструкция

**Files:**
- Modify: \`dzen_commenter/orchestrator/loop.py:21-23,151-245\`
- Modify: \`tests/orchestrator/test_loop.py:646-710\`

**Interfaces:**
- Consumes: \`Comment.author: str | None\`, \`RuntimeConfigData.prompt.cta_link: str\`, \`RuntimeSettings.max_reply_length: int\`.
- Produces: \`OrchestratorLoop._format_reply_text(text: str, author: str | None) -> str\` and final \`Reply.generated_text\`.

- [x] **Step 1: Write the failing tests**

\`\`\`python
def test_run_cycle_prefixes_author_and_lowercases_ai_reply(loop_factory, comment_factory):
    comment = comment_factory(1, author="Ольга")
    harness = loop_factory(comments=[comment])
    harness.ai_provider.default_response = "Ответ про салфетки"

    harness.loop.run_cycle()

    reply = next(iter(harness.repository.replies.values()))
    assert reply.generated_text == "Ольга, ответ про салфетки"


def test_cta_candidate_instructs_ai_to_integrate_cta_without_appending(loop_factory, comment_factory):
    comment = comment_factory(1, publication_title="Идеи для ремонта")
    harness = loop_factory(comments=[comment], settings_overrides={"CTA_EVERY_N_COMMENTS": 1})
    harness.runtime_config.data.prompt.cta_link = "domeo ru"
    harness.ai_provider.default_response = "Можно обратиться на сайт domeo ru"

    harness.loop.run_cycle()

    reply = next(iter(harness.repository.replies.values()))
    assert reply.generated_text == "author-1, можно обратиться на сайт domeo ru"
    assert "domeo ru" in harness.ai_provider.calls[0][0]
    assert "отдельной строкой" in harness.ai_provider.calls[0][0]
\`\`\`

- [x] **Step 2: Run the focused tests to verify they fail**

Run: \`.venv\\Scripts\\python -m pytest tests/orchestrator/test_loop.py -k "prefixes_author or cta_candidate_instructs" -v\`

Expected: FAIL because final formatting and CTA prompt instruction do not exist.

- [x] **Step 3: Write minimal implementation**

\`\`\`python
def _format_reply_text(self, text: str, author: str | None) -> str:
    for index, character in enumerate(text):
        if character.isalpha():
            text = text[:index] + character.lower() + text[index + 1:]
            break
    return f"{author.strip()}, {text}" if author and author.strip() else text
\`\`\`

Reserve \`len(f"{author.strip()}, ")\` from the model limit before generation. Replace the literal CTA suffix and \`text += cta_suffix\` with a prompt block for selected candidates: it provides the configured CTA text and requires it to be integrated naturally, not as a separate line or isolated advertising sentence.

- [x] **Step 4: Run the focused and full orchestrator tests**

Run: \`.venv\\Scripts\\python -m pytest tests/orchestrator/test_loop.py -v\`

Expected: PASS.

- [x] **Step 5: Commit**

\`\`\`bash
git add dzen_commenter/orchestrator/loop.py tests/orchestrator/test_loop.py
git commit -m "feat: personalize replies and integrate CTA in prompt"
\`\`\`

### Task 2: Текстовое CTA-поле и промпт-конфигурация

**Files:**
- Modify: \`dzen_commenter/admin/templates/settings.html:94\`
- Modify: \`dzen_commenter/admin/validation.py:99-108\`
- Modify: \`config/runtime_config.json:13-16\`
- Modify: \`prompt_config.example.json:5-8\`
- Modify: \`tests/admin/test_settings.py:105-180\`

**Interfaces:**
- Consumes: form field \`cta_link: str\`.
- Produces: \`validate_settings_form(form)\` accepts nonempty CTA text and \`PromptBrandConfig.cta_link\` preserves it verbatim.

- [x] **Step 1: Write the failing tests**

\`\`\`python
def test_settings_page_renders_cta_text_input(client):
    response = client.get("/settings")
    assert 'name="cta_link"' in response.text
    assert 'type="text"' in response.text


def test_validate_settings_form_accepts_plain_cta_text():
    form = _form()
    form["cta_link"] = "domeo ru"

    data, errors = validate_settings_form(form)

    assert errors == {}
    assert data.prompt.cta_link == "domeo ru"
\`\`\`

- [x] **Step 2: Run focused tests to verify they fail**

Run: \`.venv\\Scripts\\python -m pytest tests/admin/test_settings.py -k "cta_text or plain_cta" -v\`

Expected: FAIL because the template uses \`type="url"\` and validation rejects non-URL text.

- [x] **Step 3: Write minimal implementation**

\`\`\`html
<label>Текст CTA<input type="text" name="cta_link" value="{{ values.cta_link }}"></label>
\`\`\`

Remove only the URL-protocol validation. In the runtime and example prompt configurations, set the sample CTA text to \`domeo ru\` and add an instruction that it must be woven into the reply naturally, never emitted as a URL, separate line, or standalone advertising sentence.

- [x] **Step 4: Run focused and relevant tests**

Run: \`.venv\\Scripts\\python -m pytest tests/admin/test_settings.py tests/prompt/test_builder.py -v\`

Expected: PASS.

- [x] **Step 5: Commit**

\`\`\`bash
git add dzen_commenter/admin/templates/settings.html dzen_commenter/admin/validation.py config/runtime_config.json prompt_config.example.json tests/admin/test_settings.py
git commit -m "feat: allow text CTA in settings"
\`\`\`

### Task 3: Полная проверка и доставка

**Files:**
- Modify: \`docs/superpowers/plans/2026-08-03-comment-reply-personalization-and-cta.md\`

**Interfaces:**
- Consumes: commits from Tasks 1–2 and the Docker Compose deployment.
- Produces: checked release commit pushed to the configured upstream and healthy remote services.

- [x] **Step 1: Run the entire suite**

Run: \`.venv\\Scripts\\python -m pytest -q\`

Expected: PASS with no test failures.

- [x] **Step 2: Inspect the release diff**

Run: \`git diff --check HEAD~2..HEAD && git status --short && git log -3 --oneline\`

Expected: no whitespace errors and only planned changes.

- [x] **Step 3: Push, deploy, and inspect the remote services**

Run: \`git push\`, then on the deployment host run \`git pull --ff-only && docker compose up -d --build && docker compose ps && docker compose logs --tail=100 app admin\`.

Expected: configured branch is accepted, \`app\` is healthy, \`admin\` is running, and neither has a startup exception.

- [x] **Step 4: Commit the completed plan**

\`\`\`bash
git add docs/superpowers/plans/2026-08-03-comment-reply-personalization-and-cta.md
git commit -m "docs: add reply personalization implementation plan"
\`\`\`
