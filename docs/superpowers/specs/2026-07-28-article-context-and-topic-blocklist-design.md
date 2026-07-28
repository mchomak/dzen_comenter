# Article context and topic blocklist design

## Goal

Give the reply model the actual text of the Dzen article for each comment, so
replies can refer to its substance rather than only its title and comment
thread. If the article cannot be read, retain the current reply flow and mark
the saved reply accordingly. Extend only the prompt's `anti_rules` section
with the requested topic blocklist.

## Scope

- Open the article URL in a separate browser tab, extract the article text,
  then close that tab in every outcome.
- Add the article URL and extracted text to the model context, with an explicit
  instruction to study the supplied article before replying.
- Fall back to the current title/thread/comment context when extraction fails.
- Persist whether article text was used for a generated reply and show it in
  the comments admin page.
- Add the requested topics to `anti_rules` in defaults, the checked-in runtime
  configuration, and the example prompt configuration. Do not rewrite the
  role, tone, or task sections.

Out of scope: changing publication statuses, browsing by the AI provider,
persisting article bodies, or changing lead classification.

## Design

### Article extraction

`DzenStudioPage` will expose a page-contract method that accepts a post URL and
returns the extracted article text or no value. It will create a new tab from
the existing authenticated browser context, navigate to the URL, and take text
from the article's content container. The temporary tab is closed in a
`finally` block, including navigation, selector, and parsing failures. This
keeps the comments page intact and prevents article tabs accumulating in memory.

The page implementation will cache each successful or failed lookup by post
URL for the process lifetime. Several comments under one article therefore use
one temporary tab and one extracted text value. The cache stores text or the
empty result; it does not retain browser pages.

### Reply generation

The comment-processing loop will request article text before building the
prompt. `PromptContext` will carry the post URL and optional article text.
When text is present, the builder will add a distinct article-context block:

- the article URL;
- an instruction to read the supplied article text before answering;
- the extracted article text.

The existing title, prior thread, target comment, role, tone, and selected task
remain present. The URL is retained as a source reference; the model is not
expected to browse it.

When the URL is absent or extraction returns no text, the builder receives no
article text and preserves the current context. The reply is still generated,
as requested.

### Reply context status

Add `article_context_status` to replies, independent of the existing reply
lifecycle status (`generated`, `published`, or `error`). New replies receive
one of these values:

- `article_text_used` — extracted article text was included in the prompt;
- `without_article_text` — the normal fallback context was used.

The ORM model, reply contract, repository insert, and an Alembic migration will
carry this value. The migration leaves historical rows without a value rather
than incorrectly marking them as fallback replies. The admin feed query will
read it and the comments table will show a secondary label: «Учтён текст
статьи», «Сгенерирован без текста статьи», or «Нет данных» for historical rows.

### Prompt topic blocklist

Only `anti_rules` changes. It will explicitly require `тип: пропуск` and an
empty `ответ` when the comment concerns any of these topics:

1. политика;
2. власть;
3. политические деятели;
4. секс;
5. наркотики;
6. медицинские препараты;
7. государственные органы;
8. зарплаты;
9. пенсии.

The same section will preserve the existing safety and length restrictions.

## Failure handling

- Failure to open, read, or parse an article does not stop a comment cycle;
  the reply is generated from the previous context and marked
  `without_article_text`.
- The temporary article page is always closed before this fallback occurs.
- An unavailable article URL follows the same fallback path without opening a
  tab.

## Verification

Tests will demonstrate that:

1. article text and URL reach the prompt, and the explicit reading instruction
   is present;
2. each temporary article tab is closed after success and after an extraction
   error, and cached URLs do not open extra tabs;
3. the fallback still generates a reply and saves `without_article_text`;
4. successful extraction saves `article_text_used`;
5. the migration/repository/admin feed expose the context status correctly;
6. every configured `anti_rules` value contains the requested blocklist and
   skip instruction;
7. the focused and full test suites pass.
