# Манифест требований

Источник: `2026-08-29-brief.md`. Строку из этого списка может снять **только пользователь**.

| ID | Из брифа (дословно) | Статус | Основание | Где |
|---|---|---|---|---|
| R01 | «исправь баг» | done | Commit `2eb93aa`: Core result now uses explicit labels/mappings; reviewer PASS and real-DB mapping check passed. | `2eb93aa` |
| R02 | «залей новый коммит с фиксом насервер» | done | `origin/main` and server checkout resolved to `2eb93aa`. | Развёртывание 2026-08-29 |
| R03 | «перезупсти docker не удляя БД» | done | Compose build/recreate completed; PostgreSQL container and volume remained running, no delete/down/volume command used. | Развёртывание 2026-08-29 |
| R04 | «в конце дождись сборки контейнеров и посмотри логи» | done | Build completed; app/postgres healthy, admin Up with HTTP 302, migration head and fresh error scan passed. | Развёртывание 2026-08-29 |
| R05 | «если появяться новые ошибки сам исправь и пройди цикл по новой пока бот не заработает как надо» | done | First deploy hit stale Chromium locks; app was stopped, three explicit lock links removed, then services reached healthy state with no fresh runtime errors. | Развёртывание 2026-08-29 |
| R06i | «используй данные от сервера … по логину и паролю» | done | SSH used transiently only for the named server; no credential value entered source, artifacts, commits or report. | Спецификация §3 |
