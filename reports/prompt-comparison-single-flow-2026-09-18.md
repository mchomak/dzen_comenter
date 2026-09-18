# Сравнение prompt: pre-batch, batch и single flow

## Основание сравнения и границы

Контрольный pre-batch baseline — Git revision `5eab5ff` (`optimize: reduce article context token usage`, 2026-08-24 22:16:29 +03). В историческом production-окне «Очищенные статьи, pre-batch» длительностью **140,51 часа** было **761 reply** и **0 ReplyError**. В этом окне batch отсутствовал; служебные маркеры `тип/ответ` и `тип/SKIP` в 761 непустом тексте также зафиксированы как 0. Источник чисел: [исторический анализ стабильности](stability-history-analysis-2026-09-17.md).

Эти измерения задают сопоставимый контрольный период для сравнения инструкций. Они **не доказывают качество модели** или причинность: история runtime prompt не версионируется, pre-durable и последующие ошибки имеют разную persistence-семантику, а ручная оценка качества в исследовании не повторялась и не использовалась как числовой рейтинг. Эти ограничения зафиксированы в [историческом анализе](stability-history-analysis-2026-09-17.md#ограничения).

## Фактические пути prompt

| Путь | Формат задания модели | Транспортный/служебный формат |
| --- | --- | --- |
| `5eab5ff`, один комментарий | [`DameoPromptBuilder`](../dzen_commenter/prompt/builder.py) собирал роль, tone of voice, anti-rules, контекст одного комментария, задачу и article context. | В версии `5eab5ff` в `builder.py` не было отдельного output-rule или batch-разметки. |
| Batch-era, введённый `2335aeb` | [`DameoBatchPromptBuilder`](../dzen_commenter/prompt/batch.py) строил несколько карточек `Cnn` для одной статьи. | Prompt требовал одну строку на карточку в виде `Cnn<TAB>REPLY<TAB>текст` либо `Cnn<TAB>SKIP<TAB>`; парсер сопоставлял эти поля с item-ами. |
| Финальный single path | [Спецификация](../.autopilot/2026-09-18-single-reply-retry-flow--wip/spec.md) предписывает один комментарий на AI-вызов. Текущий [`DameoPromptBuilder`](../dzen_commenter/prompt/builder.py) добавляет правило: вернуть только текст для публикации либо ровно `SKIP`, без заголовков и меток. | Нет `Cnn`, `REPLY` или многострочного соответствия нескольким комментариям. [`sanitize_model_reply`](../dzen_commenter/contracts/reply_text.py) оставляет `None` только для исходного точного `SKIP`; recognised legacy labels удаляются, а пустой/protocol-only результат становится непубликуемым. |

## Источник утечки меток

Batch-path добавил в instruction транспортные метки `Cnn`, `REPLY` и `SKIP`. Историческое исследование формулирует более узкий вывод: наиболее вероятным источником массовых `тип: … ответ:` был prompt до `8dc494c`, который одновременно требовал транспортный формат `Cnn | текст` и поля `тип:`/`ответ:` в карточках. Это не является измерением качества модели.

Commit `8dc494c` добавил финальное правило «после `Cnn |` — только готовый текст или `SKIP`» и sanitation перед публикацией. В текущем single builder то же ограничение дано без batch-префикса; [`reply_text.py`](../dzen_commenter/contracts/reply_text.py) дополнительно удаляет распознанные старые leading labels. [Тест sanitizer](../tests/prompt/test_reply_text.py) задаёт случаи `Ответ: Готовый текст`, `Тип ответа: Ответ: Готовый текст` и protocol-only значения; [orchestrator test](../tests/orchestrator/test_loop.py) задаёт `Ответ: SKIP` как generation retry без publication job.

## Сохранённая очистка article context

Очистка контекста статьи происходит в [`DzenStudioPage.fetch_article_text`](../dzen_commenter/dzen/page.py): результат кэшируется по URL, а `_extract_article_text` извлекает структурные article blocks, исключает `FIGCAPTION` и promotional markers. Этот код добавлен в `5eab5ff`, а не является частью batch protocol. [Тест article extraction](../tests/dzen/test_dzen_page.py) ожидает сохранение title/content/blockquote и удаление figcaption и рекламного блока; [single-flow test](../tests/orchestrator/test_loop.py) задаёт очищенный текст статьи и проверяет его передачу в prompt context.

Следовательно, final single flow сохраняет article cleanup и cache, но не наследует batch-карточки и их transport protocol.

## Post-deploy checklist for orchestrator — не выполнено этим отчётом

- [ ] Зафиксировать результат свежего локального test run.
- [ ] Перед deploy создать свежий `pg_dump -Fc` и зафиксировать успешный `pg_restore --list`.
- [ ] Выполнить разрешённый deploy и дождаться health всех compose-контейнеров.
- [ ] Сохранить migration/startup logs и подтвердить runtime config без batch settings.
- [ ] Выполнить read-only DB smoke query и записать её outcome.

Этот отчёт не содержит доказательства выполнения deploy, backup, запуска контейнеров, тестов или production smoke-проверок.
