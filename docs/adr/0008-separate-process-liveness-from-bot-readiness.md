# 0008 — Separate process liveness from bot readiness

- Status: Accepted

## Context

The admin process can be running while the worker is unauthenticated, failing cycles, or no longer completing work. Reporting only process/container state does not tell an operator whether the bot is operational.

## Decision

Expose admin process liveness separately from bot readiness. The existing admin health route reports admin liveness. A separate, secret-free bot health view reports worker readiness from its heartbeat, recent cycle outcome, and positively confirmed authentication state.

Keep the readiness states explicit: starting before a completed cycle, operational after a recent successful cycle with an authenticated session, authentication-required when the session is unauthenticated, degraded after a recent cycle failure, and stale when the heartbeat exceeds its freshness rule. Use this same bot-readiness decision for the Compose app healthcheck. Admin startup does not wait for worker readiness.

## Consequences

- Operators can distinguish a running process from a bot that can currently process work.
- Admin availability does not depend on the worker being ready.
- The worker owns its readiness snapshot, while the admin exposes it read-only and without secrets.
