window.STATE =
{
  "slug": "article-context-audit",
  "title": "Аудит контекста статей Дзена",
  "mode": "full",
  "depth": "normal",
  "polish": null,
  "tier": null,
  "briefFile": "2026-08-22-brief.md",
  "memoryFile": "AGENTS.md",
  "startedAt": "2026-08-22T15:41:05+03:00",
  "updatedAt": "2026-08-22T15:48:21+03:00",
  "finishedAt": "2026-08-22T15:48:21+03:00",
  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-08-22T15:41:05+03:00", "finishedAt": "2026-08-22T15:42:00+03:00" },
    { "id": "manifest", "status": "done", "startedAt": "2026-08-22T15:42:00+03:00", "finishedAt": "2026-08-22T15:43:00+03:00" },
    { "id": "briefing", "status": "skipped", "startedAt": "2026-08-22T15:43:00+03:00", "finishedAt": "2026-08-22T15:44:00+03:00", "note": "полный автомат — самобрифинг" },
    { "id": "spec", "status": "done", "startedAt": "2026-08-22T15:44:00+03:00", "finishedAt": "2026-08-22T15:46:00+03:00", "note": "G2: выборка URL сделана условной и проверяет непустой article text" },
    { "id": "plan", "status": "skipped", "startedAt": "2026-08-22T15:46:00+03:00", "finishedAt": "2026-08-22T15:46:20+03:00", "note": "ярус T0 — аудит без production-кода" },
    { "id": "build", "status": "done", "startedAt": "2026-08-22T15:46:20+03:00", "finishedAt": "2026-08-22T15:47:00+03:00", "note": "статический аудит и существующие тесты" },
    { "id": "review", "status": "done", "startedAt": "2026-08-22T15:47:00+03:00", "finishedAt": "2026-08-22T15:47:24+03:00", "note": "G4: независимая сверка кода" },
    { "id": "final", "status": "done", "startedAt": "2026-08-22T15:47:24+03:00", "finishedAt": "2026-08-22T15:48:21+03:00", "note": "отчёт и G4-сверка завершены" }
  ],
  "requirements": { "total": 5, "done": 2, "inTicket": 0, "inSpec": 0, "placeholder": 2, "deferred": 1, "dropped": 0 },
  "tickets": [],
  "singlePass": { "startedAt": "2026-08-22T15:46:20+03:00", "finishedAt": "2026-08-22T15:47:00+03:00", "files": [".autopilot/article-context-audit/interfaces.md", ".autopilot/article-context-audit/report.md"], "tests": { "passed": 378, "failed": 0 }, "commit": "8bbe67a" },
  "tests": { "passed": 378, "failed": 0, "skipped": 24 },
  "debt": { "placeholders": ["R02 — read-only выборка production URL", "R04 — измерение фактического текста и шума"], "assumptions": ["SSH-доступ по ключу недоступен; пароль из переписки не используется."], "emptyEnv": [] },
  "additions": [],
  "coverage": { "findings": 2, "resolved": ["R02 выполняется только после подтверждения отсутствия article text в БД.", "Текстовая статья проверяется непустым результатом текущего extractor."] },
  "blind": { "verdict": "R01 и R05 выполнены; R02 и R04 не выполнены без production-доступа; R03 отложен до live-запуска точного extractor.", "drift": [] }
}
