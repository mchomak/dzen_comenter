# 0010 — Bound Reply Expansion
- Status: Accepted

## Context

A target-reply search can expand threads in unrelated cards. Hidden replies in the target publication still need to be found, and a full-feed collection still needs to process replies in newly loaded cards.

## Decision

During publication-scoped search, expand threads only in cards from the target publication. During full-feed collection, expand each new card no more than once per pass. Keep the existing time limit fail-closed.

## Why

This preserves discovery of hidden replies in the target publication and newly loaded cards while limiting repeated and global processing.

## Consequences

Publication-scoped search does not expand threads in unrelated cards. During a full-feed collection cycle, reloading an already processed card does not trigger another expansion pass for that card.
