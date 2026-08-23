window.STATE =
{
  "slug": "live-article-context-audit",
  "title": "Live-аудит текста статей Дзена",
  "mode": "full",
  "depth": "normal",
  "polish": null,
  "tier": null,
  "briefFile": "2026-08-23-brief.md",
  "memoryFile": "AGENTS.md",
  "startedAt": "2026-08-23T13:24:24+03:00",
  "updatedAt": "2026-08-23T13:30:30+03:00",
  "finishedAt": "2026-08-23T13:30:30+03:00",
  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-08-23T13:24:24+03:00", "finishedAt": "2026-08-23T13:24:40+03:00" },
    { "id": "manifest", "status": "done", "startedAt": "2026-08-23T13:24:40+03:00", "finishedAt": "2026-08-23T13:24:55+03:00" },
    { "id": "briefing", "status": "skipped", "startedAt": "2026-08-23T13:24:55+03:00", "finishedAt": "2026-08-23T13:25:10+03:00", "note": "полный автомат — внешний доступ уже определён" },
    { "id": "spec", "status": "done", "startedAt": "2026-08-23T13:25:10+03:00", "finishedAt": "2026-08-23T13:26:00+03:00", "note": "G2: покрытие подтверждено" },
    { "id": "plan", "status": "skipped", "startedAt": "2026-08-23T13:26:00+03:00", "finishedAt": "2026-08-23T13:26:10+03:00", "note": "ярус T0 — единственный audit pass" },
    { "id": "build", "status": "failed", "startedAt": "2026-08-23T13:26:10+03:00", "finishedAt": "2026-08-23T13:27:00+03:00", "note": "нет безопасной SSH-аутентификации" },
    { "id": "review", "status": "skipped", "startedAt": "2026-08-23T13:27:00+03:00", "finishedAt": "2026-08-23T13:30:00+03:00", "note": "production pass не состоялся" },
    { "id": "final", "status": "done", "startedAt": "2026-08-23T13:30:00+03:00", "finishedAt": "2026-08-23T13:30:30+03:00", "note": "G4 подтвердил незакрытый внешний blocker" }
  ],
  "requirements": { "total": 4, "done": 0, "inTicket": 0, "inSpec": 0, "placeholder": 1, "deferred": 3, "dropped": 0 },
  "tickets": [],
  "singlePass": null,
  "tests": { "passed": 27, "failed": 0, "scope": "tests/dzen/test_dzen_page.py" },
  "debt": { "placeholders": ["R01 — настроенный SSH-ключ или сертификат"], "assumptions": [], "emptyEnv": [] },
  "additions": [],
  "coverage": { "findings": 0, "resolved": ["Независимая сверка: бриф полностью покрыт спецификацией."] },
  "blind": { "verdict": "R01, R03 и R04 не выполнены; R02 подтверждён только на fake-страницах.", "drift": [] }
}
