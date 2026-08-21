# Границы, решённые в спецификации

## Технологии и правила

- Python 3.11, FastAPI/Jinja2, Pydantic settings, httpx и Playwright; существующий `pytest` — тестовая команда.
- Не записывать логин или пароль Яндекс Дзена в JSON, логи, редиректы, ответы или `.autopilot/`.
- Не запускать реальную авторизацию Яндекс Дзена и не выполнять HTTP-запрос к нему во время проверки этой работы.
- Не менять существующие пользовательские настройки, кроме добавления полей, описанных в спецификации.

## Границы из спецификации

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| runtime config | живые несекретные настройки | `RuntimeConfig.get/save` | JSON и mtime-кэш |
| admin settings | HTML-формы и валидация ввода | `POST /settings`, `POST /settings/dzen-account` | детали JSON и Unix-сокета |
| login control | безопасная одноразовая передача команды | `DzenLoginControlClient.login`, `DzenLoginControlServer.serve_in_thread` | framing socket и таймауты |
| browser session | жизненный цикл и вход браузера | `change_account(login, password) -> bool` | Playwright context/profile/state |
| Telegram transport | отправка в Telegram с актуальным proxy | `notify`, `notify_error` | HTTP-клиент и его пересоздание |
| supervision | подавление дубликатов аварий | `run_supervised(..., error_notification_cooldown_provider=...)` | отсчёт времени и сигнатуры |

Тестовые швы: `RuntimeConfig`, `TelegramNotifier` с injected client, `run_supervised`, и публичные client/server socket и `change_account`. Реальный браузер и Яндекс не запускать.

## Из таска 01 — настройки

- `RuntimeSettings.error_notification_cooldown_seconds: int = 900` и `RuntimeSettings.telegram_proxy_url: str = ""` — новые runtime-поля с обратной совместимостью для старых JSON.
- `POST /settings` принимает и возвращает формат длительности `N[mh]` и URL Telegram proxy.

## Из таска 02 — hot reload мониторинга

- `run_supervised(..., error_notification_cooldown_provider=...)` читает live cooldown для повторной одинаковой ошибки.
- `TelegramNotifier(..., proxy_url_provider=None)` и `TelegramAuthAssistant(..., proxy_url_provider=None)` пересоздают HTTP-клиент при смене proxy URL.

## Из таска 03 — смена аккаунта Дзена

- `POST /settings/dzen-account` вызывает `DzenLoginControlClient.login(login, password) -> bool` без сохранения учётных данных.
- `DzenLoginControlServer.serve_in_thread() -> threading.Thread` обрабатывает JSONL `{"action":"login","login":str,"password":str}` и возвращает `{ "ok": bool, "error"?: str }`.
- `PlaywrightSessionManager.change_account(login, password) -> bool` пересоздаёт browser profile и выполняет вход с одноразовыми аргументами.

## Из таска 05 — аварийные каналы

- `TelegramNotifier.notify_error(message, error)` независимо пытается доставить аварийное сообщение в Telegram и в настроенный email fallback.
