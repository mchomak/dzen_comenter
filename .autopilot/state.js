window.STATE = {
  "slug": "double-publication-verification",
  "dir": "2026-10-02-double-publication-verification--wip",
  "title": "Двойная проверка публикации ответа в Дзене",
  "mode": "full",
  "depth": "normal",
  "polish": null,
  "tier": "T1",
  "briefFile": "2026-10-02-brief.md",
  "memoryFile": "AGENTS.md",
  "skillDir": "C:/Users/McHomak/.agents/skills/autopilot",
  "startedAt": "2026-10-02T15:20:16+03:00",
  "updatedAt": "2026-10-03T11:42:06+03:00",
  "finishedAt": "2026-10-03T11:42:06+03:00",
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-10-02T15:20:16+03:00",
      "finishedAt": "2026-10-02T15:51:29+03:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "finishedAt": "2026-10-02T15:51:29+03:00"
    },
    {
      "id": "briefing",
      "status": "skipped",
      "note": "full mode self-briefing"
    },
    {
      "id": "spec",
      "status": "done",
      "finishedAt": "2026-10-02T15:51:29+03:00"
    },
    {
      "id": "plan",
      "status": "done",
      "finishedAt": "2026-10-02T15:51:29+03:00"
    },
    {
      "id": "build",
      "status": "done",
      "startedAt": "2026-10-02T15:51:29+03:00",
      "finishedAt": "2026-10-03T11:42:06+03:00"
    },
    {
      "id": "review",
      "status": "done",
      "finishedAt": "2026-10-03T11:42:06+03:00"
    },
    {
      "id": "final",
      "status": "done",
      "finishedAt": "2026-10-03T11:42:06+03:00"
    }
  ],
  "requirements": {
    "total": 8,
    "done": 8,
    "inTicket": 0,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "Double publication confirmation",
      "status": "done",
      "startedAt": "2026-10-02T15:51:29+03:00",
      "repairs": 1,
      "handoffs": 0,
      "finishedAt": "2026-10-03T11:42:06+03:00",
      "commit": "056ac6c + 45f76cd",
      "review": "PASS",
      "tests": {
        "passed": 485,
        "skipped": 47,
        "failed": 0
      }
    }
  ],
  "singlePass": null,
  "tests": {
    "passed": 485,
    "skipped": 47,
    "failed": 0,
    "warnings": 1
  },
  "debt": {
    "placeholders": [],
    "assumptions": [],
    "emptyEnv": []
  },
  "additions": [],
  "coverage": null,
  "concerns": [
    {
      "status": "report",
      "note": "Git directory rename returned Permission denied. Canonical path retains --wip; finishedAt and all statuses are final."
    }
  ],
  "reviewers": {
    "manifestSpec": {
      "status": "pass",
      "agent": "stage_tester"
    },
    "craft": null
  },
  "blind": {
    "status": "pass",
    "matched": 8,
    "checked": 8,
    "mismatches": [],
    "liveResult": "1 POST, HTTP 200, Studio and article confirmed. Final saved-session read-only verification: reply found, 0 POST."
  }
};
