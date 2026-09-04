# Границы, решённые в спецификации

- `CommentRepository.claim_next_batch` владеет выбором готового пакета по БД, времени и квоте. Он не принимает решение по DOM-снимку.
- `OrchestratorLoop._generate_batch` владеет обработкой захваченного пакета: он создаёт outcomes для недоступных элементов, генерирует только доступную часть и сохраняет итоговый набор outcomes в исходном порядке.
- `DzenPage.publish_reply` остаётся единственной операцией браузерной отправки и не вызывается для отсутствующего в текущем снимке элемента.

## Шов для тестов

`OrchestratorLoop.run_cycle` с фейковыми `CommentRepository`, `DzenPage` и AI-провайдером из `tests/orchestrator/conftest.py`. Тесты не запускают реальный браузер и не обращаются к Дзену.

## Команды

- Точечный цикл: `.venv\Scripts\python.exe -m pytest tests/orchestrator/test_loop.py -q`
- Полный набор: `.venv\Scripts\python.exe -m pytest -q`
