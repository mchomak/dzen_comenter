# Comments Admin Refinement Design

## Goal

Make the comments page denser and easier to scan while preserving its
server-rendered FastAPI/Jinja implementation.

## Decisions

- The author control is a native `input` with a `datalist` of unique authors
  from the displayed feed. Browser filtering narrows the suggestions as the
  administrator types; submitting the form retains the existing
  case-insensitive substring filter.
- The table remains five columns. Header labels are horizontally centred, as
  are the compact metadata cells.
- Each dialogue begins with a linked post title, then shows the chronological
  human conversation and bot answer as an indented compact thread. The
  currently processed comment is visually distinct and the bot answer is
  visibly attached to it.
- New crawled comments persist the publication title directly with the comment
  so that the title matches the particular post. Existing rows have no
  recoverable title and show their safe post link with the label `Открыть пост`.
- Timestamps are formatted to whole seconds; no stored values or ordering logic
  changes.

## Data Flow

The crawler already reads `publication_title` for each Dzen post group. A new
nullable `comments.post_title` column receives that value during comment upsert.
The admin feed selects it alongside the existing post URL and maps it to the
template. The template renders a safe, new-tab post link at the top of the
dialogue only when both a title and safe Dzen URL are available; otherwise the
existing Post-column link stays available.

## Validation

Focused admin, crawler, and repository tests cover title persistence,
backwards-compatible null titles, the author suggestion list, compact-thread
markup, centred header hook, and second-precision time rendering. The full
test suite, whitespace check, production health endpoint, comments response,
and Compose logs verify delivery.
