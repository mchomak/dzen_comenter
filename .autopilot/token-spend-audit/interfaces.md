# Границы, решённые в спецификации

## Правила аудита

- Рабочая директория: `/Users/mchomak/Projects/dzen_comenter`.
- Стек: Python 3.11, FastAPI, SQLAlchemy/PostgreSQL, Docker Compose.
- Проверка кода: `/tmp/dzen-commenter-test.3icCmB/bin/pytest` при необходимости; production-код и тесты в этом аудите не меняются.
- Сервер: только настроенный безопасный SSH-доступ; разрешены исключительно команды чтения и агрегаты БД.
- Запрещены: использование пароля из переписки, SQL-запись, удаление данных, миграции, массовое завершение процессов и изменение сетевых правил.

## Швы для проверки

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| `OrchestratorLoop` | частотой генераций и повторной генерацией | `run_cycle`, `_generate_reply` | браузерную синхронизацию и хранение ответа |
| `DameoPromptBuilder` | составом prompt | `build(PromptContext) -> str` | форматирование блоков контекста |
| `GigaChatProvider` | один API-вызов модели | `generate(prompt, temperature, max_tokens) -> str` | OAuth и HTTP retries |
| PostgreSQL repository | агрегаты ответов | read-only SQL по `comments` и `replies` | данные конкретных пользователей |
