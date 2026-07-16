# Уведомления разработчика о критических ошибках

## Цель

Отправлять разработчику все ошибки уровня `ERROR` и `CRITICAL`, включая недоступность Dzen, Telegram и прокси. Основной канал — личный Telegram-разработчика; при любой ошибке Telegram-доставки сообщение отправляется всем адресатам из email fallback.

## Конфигурация

- `DEVELOPER_TELEGRAM_CHAT_ID` — личный Telegram chat ID разработчика.
- `EMAIL_FALLBACK_LIST` — comma-separated список email-адресов.
- Существующие `SMTP_*` переменные задают SMTP-доставку.

`TELEGRAM_CHAT_ID` остаётся настройкой Telegram auth command, а критические уведомления используют отдельный developer ID.

## Архитектура и поток

`DeveloperNotifier` реализует общий контракт уведомлений и содержит Telegram notifier с email fallback. Все существующие вызовы `notify_error` используют этот канал. Отдельный logging handler перехватывает только записи `ERROR`/`CRITICAL` и передаёт их в notifier. Записи ниже `ERROR` не отправляются.

Сбой Telegram API, сети или прокси переключает доставку на email. Сбой email не должен ронять основной цикл и логируется локально. Ошибка доставки собственного уведомления не создаёт новое уведомление, чтобы избежать рекурсии.

## Критерии приёмки

- `ERROR` и `CRITICAL` log records доставляются в Telegram developer chat.
- `INFO`, `WARNING` и ниже не доставляются как критические уведомления.
- Ошибка Telegram API или proxy приводит к одному email уведомлению на каждый адрес из `EMAIL_FALLBACK_LIST`.
- Ошибка SMTP не прерывает основной supervised loop.
- Auth command продолжает использовать `TELEGRAM_CHAT_ID`, а error notifications — `DEVELOPER_TELEGRAM_CHAT_ID`.
- Полный набор тестов проекта проходит.
