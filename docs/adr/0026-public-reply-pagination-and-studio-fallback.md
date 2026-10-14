# ADR-0026 — Public reply pagination and unconfirmed Studio submission
- Status: Accepted

## Context

Public article reply controls can load successive pages of children. A single click per root left a Studio-visible source hidden on the public page and blocked a saved reply before submission. The user explicitly requested repeated expansion and submission through Studio when public source search is exhausted.

## Decision

Reacquire public roots and controls after every expansion, inspect the loaded children again, and continue through visible reply controls until the source is found or no progressing control remains. Skip collapse controls and retain bounded waits and the verification deadline.

Distinguish exhausted public source search from navigation, configuration, reading and click failures. Exhausted public search permits one submission only when the exact source is already found in Studio. The existing claim token and durable `submitting` marker still precede the send action. An exact reply already visible in Studio prevents another send.

After this fallback, preserve the existing terminal `publication_unconfirmed` / `unconfirmed` outcome, including when Studio shows the reply. Do not run another full public scan or automatically send or generate the reply again. Normal public confirmation still produces `published`.

## Consequences

The public-source prerequisite in ADR-0014 and ADR-0017 is superseded. The exact Studio source requirement and durable duplicate guard remain. A fallback does not establish public visibility or exclude a reply hidden on both surfaces; terminal unconfirmed state prevents this job from sending again automatically.
