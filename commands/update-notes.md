---
description: Bring this project's continuity notes (DEV_NOTES / CHANGELOG / LESSONS / TODO) fully up to date on demand — the same pass the continuity watchdog forces automatically.
---

You've been asked to bring this project's **session-continuity notes** fully up to date — the same pass the continuity watchdog forces automatically when context fills, run now on demand.

FIRST, get TODAY's real date: run `date +%Y-%m-%d` (macOS/Linux) or `Get-Date -Format yyyy-MM-dd` (Windows PowerShell) and use that exact value for every stamp below. Do NOT trust your own sense of the date — it drifts, especially after a compaction.

In the CURRENT project folder (project root AND any `memory/` subfolder — match where these files already live; create them if missing). These docs have DIFFERENT jobs; keeping them distinct is what stops OLD notes from bleeding into what reads as "current state":

- **DEV_NOTES.md = CURRENT STATE ONLY** — a LIVING doc you OVERWRITE in place. Put `_Last updated: <date>_` at the top. BEFORE adding anything, RE-READ what's there and DELETE or CORRECT every line that is no longer true — never leave a stale "current state" sitting beside the new one. This file is NOT an archive: if a superseded fact is worth keeping, move it to CHANGELOG. Cover architecture, what's in progress, key decisions + WHY, gotchas, the file/module map, and how to build/run/test. If you can't verify an existing line, mark it `[stale? unverified <date>]` rather than letting it read as fact.
- **CHANGELOG.md = DATED HISTORY** — append-only, newest on top. Add a `## <date>` entry for what actually changed this session (files, behavior, IDs, config). This is WHERE old state belongs, so it stops contaminating DEV_NOTES.
- **LESSONS.md = ACCUMULATED GOTCHAS** — append-only, NEVER pruned (a growing knowledge base, separate from CHANGELOG). If a non-trivial bug was resolved this session, add a dated entry: `## [<date>] <short title>`, then `Symptom:` / `Root cause:` / `Fix:` / `Prevent:`. This is what lets a future session avoid re-hitting the same bug and trace "X broke Y" causation.
- **TODO.md = OPEN ITEMS ONLY** — stamp new items `(<date>)`; check off or DELETE anything now done or obsolete; give the precise next step + blockers, actionable cold.
- Any other notes that drifted (README, relevant `memory/` files).

EVIDENCE DISCIPLINE: write only what you can stand behind. State facts, not guesses; if a claim is unverified, label it as such rather than letting it read as confirmed. Write so a brand-new person — or a fresh Claude with zero memory of this conversation — could resume tomorrow and know EXACTLY what is true NOW.

When the notes are complete and saved, give a one-line summary of what you updated.
