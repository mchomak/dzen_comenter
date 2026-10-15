# ADR-0027 — Submit through Studio before public confirmation

- Status: Accepted
- Date: 2026-10-15

## Context

Public article comments sometimes fail to load. Requiring a public source and duplicate-reply search before submission exhausted publication retries while the exact source remained available in Studio. The user also reports that the bot's replies are not reliably displayed in Studio, so Studio reply visibility cannot establish success or prevent submission.

## Decision

Require the exact source comment in Studio, then submit the saved reply there. Do not inspect Studio for the bot's existing replies, and do not open the public article for reply verification before sending. Article-text retrieval for generation remains a separate operation.

Keep the durable publication claim token and `submitting` marker immediately before the send click. The managed queue and database remain responsible for idempotency. Observe the creation response without polling Studio reply visibility.

After sending, run the existing bounded public verification, including repeated reply pagination. A public match completes the job as `published`. A missing source or reply, failed article navigation, comments loading failure or other verification failure completes it as `publication_unconfirmed` / `unconfirmed`. An ambiguous send also remains unconfirmed. These outcomes do not automatically resend or regenerate the reply. Failures before a possible send retain the existing retry policy.

Draft preparation remains separate and does not verify publicly. This decision supersedes the public preflight and Studio own-reply checks in ADR-0026 and the earlier prerequisites documented in ADR-0014 and ADR-0017.

## Consequences

Public comment availability no longer blocks the first Studio submission. Public confirmation can fail after a successful send, so unconfirmed is distinct from a failed submission. Removing the DOM duplicate checks means an identical manual or external reply absent from the managed database may not be detected; the user explicitly chose this tradeoff. Existing completed and unconfirmed jobs must not be requeued automatically.
