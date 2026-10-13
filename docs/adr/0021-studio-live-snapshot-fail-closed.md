# ADR-0021 — Fail closed on invalid Studio snapshot counts

## Контекст

Сбор комментариев Studio на live Playwright-пути уже читает их содержимое единым DOM snapshot. Удержание `ElementHandle`-списков добавляет давление на память Chromium, а ошибка live-счётчика не должна переводить этот путь к сбору handles.

## Решение

Проверять корректность live-счётчиков и их согласованность со снимком групп. При некорректном или несовпадающем результате завершать scan безопасной ошибкой либо повторять чтение только через handle-free evaluate; не переходить к `COMMENT_NODE` ElementHandle. Fake/non-live fallback сохраняет сбор комментариев и контекста, когда live snapshot API недоступен.

## Почему

Это сохраняет единый live DOM snapshot без ненужных ссылок на comment nodes и не позволяет ошибке счётчика незаметно включить memory-heavy fallback. Совместимость fake/non-live страниц остаётся отдельным путём.

## Последствия

Некорректный live count прекращает scan безопасно, а страницы без live snapshot API продолжают использовать прежний fallback.
