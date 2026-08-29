# Интерфейсы

- `PostgresCommentRepository.claim_next_batch(...) -> ClaimedBatch | None` остаётся существующим контрактом. Он возвращает `None` при отсутствии готовой группы либо `ClaimedBatch`, где `items` принадлежат одному `post_url` и нумеруются последовательно.
- SQLAlchemy result layout остаётся внутренней деталью repository. Тест проверяет только возвращаемый batch и состояние очереди через существующий test fixture.
