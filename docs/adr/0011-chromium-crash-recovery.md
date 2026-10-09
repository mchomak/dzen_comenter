# 0011 — Recover from a Chromium Renderer Crash
- Status: Accepted

## Context

A Chromium renderer crash can interrupt a cycle. Waiting for the scheduled keep-alive delays replacement of the browser session, while retrying a send with an uncertain outcome can create a duplicate reply.

## Decision

Recreate the session immediately only for recognized renderer errors. After recovery, fail the current cycle and let the normal schedule retry it. Do not repeat a publication submit once its start has been marked.

## Why

A confirmed crash calls for prompt session recovery. The send boundary must preserve duplicate protection when the outcome is uncertain.

## Consequences

The current cycle does not continue after recovery. A later retry follows the normal schedule, and a submit whose start was already marked is not automatically repeated.
