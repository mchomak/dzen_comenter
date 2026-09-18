# 04 — Prompt comparison and release evidence

**Требования:** R10, R11, R12, R13, R14, R15
**Blocked by:** 01, 02, 03
**Зона:** `reports/`, `docs/research/`, test/deploy evidence
**Волна:** 3
**Status:** ready

## Что должно заработать

В репозитории есть короткий фактологический отчёт, объясняющий отличие `5eab5ff`, batch-era prompt и final single prompt на уже исследованном стабильном окне. Итоговая поставка содержит нужные исследования и доказательства проверок.

## Из брифа, дословно

> «сравни инструкции которые мы довали модели сейчас и раньше (на той выборке которую ты разбирал в иследовании, где меньше ошибок)»
> «общий коммит (иследования тоже туда закинь) и деплой на сервере, потом дождись пока поднимуться контейнеры и проверь чтобы все работало корректно»

## Разделы спецификации

Истории 8–12; «Prompt и article context».

## Критерии приёмки

- [ ] Markdown report compares the relevant prompt revisions against the exact pre-batch stable window already quantified in the historical study and names evidence/limits without inventing model-quality measurements.
- [ ] Report explains the final single output contract and which prompt path caused the batch labels; article cleanup retention is stated with a code/test reference.
- [ ] Release evidence lists the fresh local test result, backup verification, production container health/startup evidence and read-only runtime/DB smoke outcome.
- [ ] The final shared commit stages production code/tests, this report and existing research reports, while excluding pre-existing unrelated dashboard/gitignore edits and secrets.
