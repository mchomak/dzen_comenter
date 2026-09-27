# 0006 — Require positive evidence at Dzen boundaries

- Status: Accepted

## Context

An attempted reply can be ambiguous: a click does not prove that Dzen stored or displayed it. Likewise, a page without an obvious login form does not prove that the browser session is authenticated. Treating either absence of failure as success can mark an unpublished reply as published or let processing continue with an invalid session.

## Decision

Accept Dzen publication and authentication state only from positive evidence owned by the corresponding Dzen integration boundary.

`DzenStudioPage` confirms publication by inspecting the source comment's thread on the comments page and matching both the configured bot author and exact reply text after whitespace normalization. It uses the known `editor--comments-page__commentNode-*` comment wrapper and fails closed if the thread cannot be inspected. A submission acknowledgment is followed by a fresh page/thread check; an ambiguous result remains retryable, and every retry checks for an already-visible matching reply before clicking again.

`PlaywrightSessionManager` confirms authentication only when the expected comments page and positive authenticated-page evidence are present. Redirects, login overlays, and ambiguous page states are unauthenticated and use the existing restore or login path.

## Consequences

- A click or an inconclusive page state cannot complete a publication or establish an authenticated session.
- Retries avoid duplicate replies by checking for the matching reply before submitting again.
- Changes to Dzen markup or authentication signals must preserve these positive-evidence checks; missing evidence remains a non-success outcome.
