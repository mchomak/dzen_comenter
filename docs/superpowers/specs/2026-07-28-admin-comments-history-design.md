# Admin Comments History Design

## Goal

Keep the existing comments feed as the admin home page and add a separate
full-history comments page with date filtering, sort control, reliable Dzen
post links, and Moscow time stored consistently in the database.

## Routes and navigation

- `GET /` renders the existing comments feed unchanged: the latest 100
  comments, its existing status and author filters, and its current table.
  Navigation labels this page `Главная`.
- `GET /comments` renders the new history page. It has its own template and
  includes every matching comment; no 100-row limit applies.
- The existing summary dashboard is removed from the primary route and
  navigation. Settings remains available at `/settings`.

## History page

The page preserves the existing status and author filters and adds:

- optional `date_from` and `date_to` HTML date inputs, applied to
  `comments.fetched_at`;
- `date_from` as an inclusive start at `00:00:00` Moscow time;
- `date_to` as an inclusive calendar day, implemented as a strict bound before
  the following day at `00:00:00` Moscow time;
- optional filters independently: either endpoint, both endpoints, or neither;
- an `order` control with `desc` (newest first, default) and `asc` (oldest
  first);
- a result count representing the number of rendered matching rows.

The database query, not a Python post-filter, applies the date bounds and sort
order so the full history remains correct as it grows. Invalid date or order
parameters fall back safely to no date bound and descending order.

## Moscow time persistence

The database uses timezone-naive `timestamp` columns. They will represent
Moscow wall-clock time consistently.

- A shared time helper returns the current `Europe/Moscow` time without
  timezone information for persistence.
- Dzen relative timestamps are calculated from the same Moscow clock.
- Reply creation time uses that helper.
- An Alembic migration shifts all non-null existing values by three hours in:
  `comments.posted_at`, `comments.fetched_at`, `replies.published_at`, and
  `replies.created_at`.

The migration's downgrade subtracts the same three-hour interval. Admin
templates display stored times directly, so all shown times are Moscow time.

## Post links

The crawler keeps the current post-link selector and adds a fallback that finds
a Dzen article anchor within the post group. Both paths normalize relative URLs
to an absolute `https://dzen.ru/a/...` URL and reject non-Dzen or non-article
targets. Newly collected comments therefore retain a usable article URL.

Historical rows whose `post_url` is empty cannot be recovered from the current
schema and continue to show `Ссылка отсутствует` accurately.

## Verification

Tests will cover the route split, unbounded history query, independent date
bounds, inclusive end-date behavior, both sort orders, Moscow clock usage,
the data migration, and fallback post-link extraction. The focused suites and
the full pytest suite must pass.
