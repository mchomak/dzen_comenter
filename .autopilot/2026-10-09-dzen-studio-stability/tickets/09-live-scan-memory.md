# 09 — Убрать удержание DOM-ссылок при чтении Studio

**Требования:** G01, R08
**Blocked by:** 07
**Зона:** `dzen_commenter/dzen/`, `tests/dzen/`
**Волна:** 7
**Status:** done

## Что должно заработать

Полный live-снимок комментариев Studio не должен дополнительно сохранять Playwright-ссылки на каждый DOM-узел комментария. Счётчики и данные снимка остаются точными. Резервный путь для страниц, где live-снимок недоступен, сохраняет прежнее поведение.

## Из брифа, дословно

> «бот шлет кучу ошибок зайди проверь что не так и сиправь»
> «получать данные ленты одним DOM-снимком»

## Разделы спецификации

Истории 1, 8–9; Решения 6, 9, 12, 14–15.

## Критерии приёмки

- [x] На live Playwright-пути подсчёт комментариев не создаёт и не удерживает `ElementHandle`-списки между обычными проходами снимка.
- [x] Регрессионный тест фиксирует отсутствие выборки `COMMENT_NODE` через Playwright handles на live-пути и точность счётчиков снимка.
- [x] Когда live-снимок недоступен, fallback по fake/non-live странице продолжает собирать комментарии и их контекст.
- [x] Узкие тесты Studio и полный набор проходят; реальные запросы и публикации в Dzen не выполняются.

## Проверка и ревью

- `python -m pytest -q tests/dzen/test_dzen_page.py -k "fetch_comments or studio_feed_snapshot or studio_feed_scroll"` — 37 passed.
- `python -m pytest -q` — 617 passed, 51 skipped.
- `python -m ruff check dzen_commenter/dzen/page.py` — passed.
- Manifest/spec review — clean. Craft review — non-blocking findings transferred to T10: malformed live count fallback must remain handle-free; count test must use varied multi-group data.
