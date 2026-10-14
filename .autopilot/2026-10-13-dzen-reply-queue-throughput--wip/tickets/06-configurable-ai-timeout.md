# 06 — Настраиваемый timeout AI-запроса

**Требования:** R01, R01.6, R01.7, A01
**Blocked by:** 01, 02
**Зона:** `dzen_commenter/config/`, `dzen_commenter/ai/`, `.env.example`, `tests/config/`, `tests/ai/`, только существующий `tests/contracts/test_foundation.py` для обновления ожидаемого списка Settings/env-ключей
**Волна:** 3
**Status:** done

## Что должно заработать

Если модель не отвечает за установленное время, попытка заканчивается предсказуемым timeout и не держит worker бесконечно. Лимит длительности можно менять в окружении; конфигурационный интерфейс лимита активного окна создаётся здесь, а его передача в runtime-обработку выполняется в Ticket 07.

## Из брифа, дословно

> «ждет таймаут (указано в настройках) если ответ не приходит, переходим к следующему»
> «проверять часовую квоту перед каждым фактическим AI-вызовом, включая повторное сокращение ответа; сохранять учёт таких вызовов между циклами и перезапусками»

## Разделы спецификации

Истории 6–7; решения «Квота и таймаут»; шов AI providers/settings; assumption A01.

## Критерии приёмки

- [x] `AI_REQUEST_TIMEOUT_SECONDS` добавлен в Settings с валидацией `> 0`, default 90 секунд и документирован в `.env.example`.
- [x] `MAX_ACTIVE_WORK_ITEMS` добавлен в Settings с валидацией положительного целого, default 30 и документирован в `.env.example`.
- [x] Provider factory передаёт timeout в активные HTTP providers OpenAI-compatible и GigaChat; connect timeout не больше общего; заглушка YandexGPT не получает фиктивной реализации.
- [x] Timeout любого provider превращается в штатную generation failure с retry/cooldown, не останавливает остальные ready jobs и не скрывает исключение.
- [x] Существующая durable quota reservation остаётся перед каждым фактическим вызовом модели, включая shortening; тесты подтверждают, что никакая ветка не обходит reservation.
- [x] Config/provider tests используют fake HTTP и не вызывают внешние AI endpoints; проверяют оба defaults и невалидные значения; scoped Ruff проходит.
- [x] Контрактный тест `tests/contracts/test_foundation.py` сверяет новые Settings-поля с эталоном `EXPECTED_FIELDS` и `.env.example`, чтобы полный набор проекта оставался согласованным.
