# 06 — Release prompt and live-status clarity

**Requirements:** R03, R09, R11, R12
**Blocked by:** 02, 03
**Zone:** `runtime_config.example.json`, `dzen_commenter/admin/`, `tests/admin/`, `tests/config/`, `tests/prompt/`
**Wave:** 5
**Status:** ready

## What must work

The distributed prompt example contains no legacy request to return a `Тип`/`Ответ` envelope or a textual `пропуск`; it tells the model to return publication text or the exact standalone `SKIP`. The final deployment updates the live runtime prompt by the same contract. The admin history displays all live comment phases rather than substituting a misleading reply state.

## Acceptance criteria

- [ ] `runtime_config.example.json` does not instruct a `Тип`/`Ответ` envelope or a textual `пропуск`, and documents exact `SKIP` as the only skip protocol.
- [ ] A regression test protects the released prompt contract and the existing output sanitizer cases for old labels/protocol-only output stay green.
- [ ] History explicitly displays `new`, `generating`, and `publishing`; it does not show “Нет ответа” or “Сгенерирован” in place of those phases.
- [ ] Focused tests are red first then green; no commit is made by this ticket.
