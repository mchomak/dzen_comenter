# Что проверено и какие границы действуют

## Текущее поведение

- `dzen_commenter.dzen.page.DzenStudioPage.fetch_article_text(post_url) -> str | None` — единственный путь, которым воркер получает текст статьи для prompt. Он создаёт отдельную Playwright-вкладку, берёт первый непустой `article`, затем `main`, затем `[class*="article"]`, и возвращает `inner_text()`.
- `dzen_commenter.orchestrator.loop.OrchestratorLoop` передаёт результат в `PromptContext.article_text`; `DameoPromptBuilder` добавляет его в prompt как «Текст статьи».
- `replies.article_context_status` — признак наличия контекста, не копия текста статьи. В `ReplyTable` нет поля `article_text` и счётчиков токенов.

## Границы аудита

- Только read-only запросы к production БД и только изолированное чтение публичных страниц тем же методом; никаких запросов к GigaChat, публикаций, запуска воркера или изменения данных.
- Если доступ к production не подтверждён ключом/сертификатом, фактические URL, тексты и долю шума не заменяются догадками.
- Предлагаемая очистка должна быть отдельной функцией в слое извлечения и тестироваться через fake Playwright-страницы; orchestration, БД и prompt API не меняются.
