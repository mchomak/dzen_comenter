# 0012 — Use Privacy-Safe Diagnostics Before Scaling
- Status: Accepted

## Context

Repeated browser work may consume server resources, but changing the server tier or memory is a paid infrastructure decision. The specification calls for optimizing browser work and reviewing new measurements before deciding whether a larger server is needed. Diagnostics must not include comment content or credentials.

## Decision

Record duration, counts of cards, comments, DOM elements, and attempts, plus available JS heap measurements. Do not record authors, text, links, tokens, or HTML. Defer any paid server-tier or memory change until after optimization and review of the new logs.

## Why

The specified sequence is to reduce repeated browser work, collect new measurements, and then decide whether a larger server is needed. The listed measurements support that assessment without recording comment content.

## Consequences

The infrastructure sizing decision remains deferred until the new logs are reviewed. Diagnostics are limited to the listed durations and quantitative measurements.
