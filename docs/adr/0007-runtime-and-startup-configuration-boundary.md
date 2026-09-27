# 0007 — Separate runtime settings from startup configuration

- Status: Accepted

## Context

The admin panel needs to change supported operational settings without restarting the worker. Credentials, tokens, proxy URLs that may contain user information, and settings that shape process or browser startup need a fixed, secret-safe source and cannot safely be treated as ordinary admin-editable values.

## Decision

Use the shared runtime JSON as the sole source for supported admin-managed settings and prompt fields. The worker reads those values through its normal runtime reload cycle.

Use environment and Compose configuration for credentials, tokens, proxy URLs, database connection inputs, browser mode, and other process-start settings. These settings require recreating the relevant service to change. Do not serialize startup secrets into runtime JSON, expose them in admin forms or health responses, or log their values.

## Consequences

- A runtime setting takes effect through the existing reload path without a container restart.
- Startup configuration changes are explicit deployment changes and require service recreation.
- The runtime configuration schema and admin UI contain only the supported non-secret live settings.
