# 0014 — Distinguish Search Exhaustion from an Unconfirmed Send
- Status: Accepted

## Context

Exhausting the source search does not prove that the source comment is absent. An uncertain outcome after a send attempt must not trigger an automatic retry that could duplicate the reply.

## Decision

When the search limit is reached, report `scan_limit_reached=true` and `absence_confirmed=false`. The public preflight check must not send a reply without the source comment. After a send attempt, preserve the existing terminal `unconfirmed` status and do not automatically retry the publication. Keep the existing finite limit on publication retries.

## Why

Reaching the limit means only that the bounded search ended; it does not confirm absence. Keeping `unconfirmed` distinct from a successful send avoids treating an uncertain outcome as grounds for automatically sending again.

## Consequences

Logs and publication state distinguish a limited search from confirmed absence. A reply is not sent without a found source comment, and an unconfirmed send is not automatically repeated.
