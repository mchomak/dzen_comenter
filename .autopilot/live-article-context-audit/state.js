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
  "updatedAt": "2026-08-23T14:09:12+03:00",
  "finishedAt": "2026-08-23T14:09:12+03:00",
  "stages": [
    { "id": "preflight", "status": "done", "startedAt": "2026-08-23T13:24:24+03:00", "finishedAt": "2026-08-23T13:24:40+03:00" },
    { "id": "manifest", "status": "done", "startedAt": "2026-08-23T13:24:40+03:00", "finishedAt": "2026-08-23T13:24:55+03:00" },
    { "id": "briefing", "status": "skipped", "startedAt": "2026-08-23T13:24:55+03:00", "finishedAt": "2026-08-23T13:25:10+03:00", "note": "полный автомат — внешний доступ уже определён" },
    { "id": "spec", "status": "done", "startedAt": "2026-08-23T13:25:10+03:00", "finishedAt": "2026-08-23T13:26:00+03:00", "note": "G2: покрытие подтверждено" },
    { "id": "plan", "status": "skipped", "startedAt": "2026-08-23T13:26:00+03:00", "finishedAt": "2026-08-23T13:26:10+03:00", "note": "ярус T0 — единственный audit pass" },
    { "id": "build", "status": "done", "startedAt": "2026-08-23T13:26:10+03:00", "finishedAt": "2026-08-23T14:08:00+03:00", "note": "read-only production extract и GigaChat token count завершены" },
    { "id": "review", "status": "done", "startedAt": "2026-08-23T14:08:00+03:00", "finishedAt": "2026-08-23T14:09:00+03:00", "note": "SHA-256 и расчёты сверены локально" },
    { "id": "final", "status": "done", "startedAt": "2026-08-23T14:09:00+03:00", "finishedAt": "2026-08-23T14:09:12+03:00", "note": "G4: все четыре требования подтверждены артефактами" }
  ],
  "requirements": { "total": 4, "done": 4, "inTicket": 0, "inSpec": 0, "placeholder": 0, "deferred": 0, "dropped": 0 },
  "tickets": [],
  "singlePass": null,
  "tests": { "passed": 27, "failed": 0, "scope": "tests/dzen/test_dzen_page.py" },
  "debt": { "placeholders": [], "assumptions": [], "emptyEnv": [] },
  "additions": [],
  "coverage": { "findings": 0, "resolved": ["SHA-256 production-выборки совпали с локальными файлами; GigaChat tokens/count дал воспроизводимые числа."] },
  "blind": { "verdict": "R01–R04 выполнены: пять URL, точный extractor, пять .txt и token-based отчёт сохранены.", "drift": [] }
}
