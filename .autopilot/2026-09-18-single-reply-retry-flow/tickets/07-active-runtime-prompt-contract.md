# 07 — Active runtime prompt contract

**Requirements:** R11, R12
**Blocked by:** 06
**Zone:** `config/runtime_config.json`, `prompt_config.example.json`, `tests/prompt/`
**Wave:** 6
**Status:** ready

## What must work

Every tracked, deployable prompt configuration uses the single-output contract: publishable reply text or the exact standalone `SKIP`. No deployed configuration asks for a `Тип`/`Ответ` envelope or textual `пропуск`.

## Acceptance criteria

- [ ] `config/runtime_config.json` has no legacy `тип:`/`ответ:`/textual `пропуск` output instruction and states the exact `SKIP` terminal skip rule.
- [ ] `prompt_config.example.json` is aligned to the same output contract.
- [ ] A regression test protects every tracked deployable prompt source from reintroducing the legacy envelope.
- [ ] Focused tests are red first then green; no commit and no server access are made by this ticket.
