# 0013 — Check and Recycle Connections in the Shared SQLAlchemy Engine
- Status: Accepted

## Context

The worker and admin application can reuse PostgreSQL idle connections that have already been closed. Both use a shared SQLAlchemy engine factory.

## Decision

Configure a connection check before reuse and periodic replacement of old connections in the shared engine factory used by the worker and admin application. Do not change the database schema.

## Why

Both parts of the application need a check for reused connections. The shared factory provides one place for this behavior. This connection-pool change does not require a schema migration.

## Consequences

The worker and admin application receive the same connection-check and recycling behavior through the shared factory. The database schema remains unchanged.
