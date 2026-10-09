# 0009 — Search Comments Within a Known Publication
- Status: Accepted

## Context

On a large feed, repeatedly checking comments that have already been seen slows the search and adds load to Chromium. Some historical saved comments may not have a publication URL, so removing feed search entirely would leave those records without a search path.

## Decision

When a publication URL is available, search for the target comment within that publication. Retain the synthetic comment ID and existing exact DOM-control checks. Keep a bounded feed-search fallback for historical records without `post_url`, and do not revisit groups already processed within one scroll series.

## Why

The known publication narrows the search and avoids rereading unrelated cards on each scroll. The bounded fallback preserves search support for historical records without a publication URL.

## Consequences

Records with a publication URL use a focused search. Records without one remain searchable through the bounded feed fallback.
