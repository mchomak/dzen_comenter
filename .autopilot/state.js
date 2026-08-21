window.STATE =
{
  "slug": "token-spend-audit",
  "title": "Аудит расхода токенов GigaChat",
  "mode": "full",
  "depth": "normal",
  "polish": null,
  "tier": "T0",
  "briefFile": "2026-08-21-brief.md",
  "memoryFile": "AGENTS.md",
  "startedAt": "2026-08-21T18:42:35+03:00",
  "updatedAt": "2026-08-21T18:53:29+03:00",
  "finishedAt": "2026-08-21T18:53:29+03:00",
  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-08-21T18:42:35+03:00", "finishedAt": "2026-08-21T18:45:00+03:00" },
    { "id": "manifest", "status": "done", "startedAt": "2026-08-21T18:45:00+03:00", "finishedAt": "2026-08-21T18:46:00+03:00" },
    { "id": "briefing", "status": "skipped", "startedAt": "2026-08-21T18:46:00+03:00", "finishedAt": "2026-08-21T18:47:00+03:00", "note": "полный автомат — самобрифинг" },
    { "id": "spec", "status": "done", "startedAt": "2026-08-21T18:47:00+03:00", "finishedAt": "2026-08-21T18:49:00+03:00", "note": "G2: доступ по паролю запрещён как небезопасный; ограничение явно внесено в спецификацию" },
    { "id": "plan", "status": "skipped", "startedAt": "2026-08-21T18:49:00+03:00", "finishedAt": "2026-08-21T18:49:30+03:00", "note": "ярус T0 — без разбивки на таски" },
    { "id": "build", "status": "done", "startedAt": "2026-08-21T18:49:30+03:00", "finishedAt": "2026-08-21T18:51:00+03:00", "note": "статический аудит и отчёт готовы" },
    { "id": "review", "status": "done", "startedAt": "2026-08-21T18:51:00+03:00", "finishedAt": "2026-08-21T18:52:00+03:00", "note": "T0: манифест, спецификация и безопасность сверены" },
    { "id": "final", "status": "done", "startedAt": "2026-08-21T18:52:00+03:00", "finishedAt": "2026-08-21T18:53:29+03:00" }
  ],
  "requirements": {
    "total": 7, "done": 5, "inTicket": 0, "inSpec": 0,
    "placeholder": 2, "deferred": 0, "dropped": 0
  },
  "tickets": [],
  "singlePass": {
    "startedAt": "2026-08-21T18:49:30+03:00",
    "finishedAt": "2026-08-21T18:51:00+03:00",
    "files": [".autopilot/token-spend-audit/", ".env.example"],
    "tests": { "passed": 80, "failed": 0 },
    "commit": null
  },
  "tests": { "passed": 378, "failed": 0, "skipped": 24 },
  "debt": { "placeholders": ["R05 — read-only серверный и БД-аудит", "R06 — остановка воркера app"], "assumptions": ["SSH-доступ по ключу недоступен; пароль из чата не используется."], "emptyEnv": [] },
  "additions": [],
  "coverage": { "findings": 1, "resolved": ["G2: безопасностное ограничение доступа вынесено в спецификацию; пароль из переписки не используется."] },
  "blind": {
    "drifts": 5,
    "summary": "Независимая проверка: 80 тестов пройдены; фактический серверный аудит, остановка воркера и подтверждённый биллинг не выполнены без безопасного доступа.",
    "tests": "80 passed in 0.20s"
  }
}
