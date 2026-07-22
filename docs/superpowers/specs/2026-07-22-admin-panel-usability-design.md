# Admin Panel Usability Design

## Goal

Make the Dzen Commenter admin panel easier to scan and use while preserving its
server-rendered FastAPI/Jinja architecture. Correct post links, retain the
conversation context that is already collected by the crawler, and organise the
settings page around the way an administrator works.

## Scope

### Shared interface

- Keep the dark blue sidebar, light neutral workspace, white cards, quiet blue
  actions, and clear hierarchy from the provided admin-panel reference.
- Apply the design consistently to the navigation, heading area, forms, tables,
  notices, buttons, and small screens. No JavaScript framework or new frontend
  dependency is introduced.
- The navigation indicates the current page. Content remains usable on narrow
  screens: the settings grid stacks and the comments table can scroll
  horizontally instead of crushing text.

### Comments

- The crawler persists each comment's preceding conversation as chronological
  `author: message` entries. The currently processed human comment remains a
  separate, visually distinct item in the table.
- Existing database rows have no recoverable history. Their history area states
  exactly: `История до комментария не сохранена`.
- The feed shows a compact `Диалог` column (the saved history plus the current
  comment), a constrained-width `Ответ бота` column, status, post, and time.
  Long messages wrap inside their cells; the table does not stretch either text
  column to consume all available width.
- Post paths discovered as `/a/<id>` are normalised to
  `https://dzen.ru/a/<id>` before being stored. The presentation layer also
  normalises legacy relative values already in the database. The column is
  named `Пост`; its `Открыть пост` link opens the publication in a new tab with
  safe `rel` attributes. Comment permalinks are not implemented.
- Reply statuses use Russian labels and coloured badges:
  `published` -> `Отправлен` (green), `generated` -> `Сгенерирован` (yellow),
  `error` -> `Ошибка` (light red), `skipped` -> `Пропущен` (gray), and a missing
  reply -> `Нет ответа` (light red). The error reason remains available beside
  the error status.

### Settings

- At desktop widths, the form is a two-column layout. The left column contains
  `Настройки бота` followed by read-only `VNC`; the right column contains
  `Промпт`.
- Checkbox text and its control share one aligned row. All labels, controls,
  legends, and help text use consistent spacing and type hierarchy.
- Prompt fields are visibly larger than the current controls and resize
  vertically. The save action remains single and is placed after both columns.
- VNC remains read-only and its existing value visibility is unchanged.

## Data and migration

- Add a nullable text column for the saved conversation history. New crawler
  writes populate it; existing records remain `NULL` so the UI can distinguish
  unavailable history from an empty conversation.
- Preserve the existing synthetic comment ID behaviour by continuing to derive
  it from the unmodified relative post path. URL normalisation is for the
  external link value only.

## Testing and deployment

- Add focused tests for crawler URL/history persistence, feed fallback and
  legacy URL normalisation, rendered labels/badges/link safety, and responsive
  layout hooks on the templates.
- Run the full test suite before committing. Push the final stage commit to the
  configured `origin` remote.
- Deploy through the existing Compose project on the configured server, rebuild
  `app` and `admin`, let Alembic apply the migration, then verify both services,
  `/health`, the comments page, and recent logs. Do not print secrets.
