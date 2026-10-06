# Интерфейсы

- `DzenStudioPage.publish_reply`: единичный submit и классификация неопределённого результата.
- `PostgresCommentRepository`: atomic переходы очереди и статусов; cooldown по next_attempt_at.
- `OrchestratorLoop`: маршрутизация unconfirmed и retry.
- `admin/comments.html`: отдельная метка.
- Реализация и критерии приёмки: `[[35-publication-unconfirmed-duplicate-guard]]` в Obsidian.
