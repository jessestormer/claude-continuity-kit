# Changelog

All notable changes to the Claude Continuity Kit are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-06-25

### Added
- **Self-calibrating watchdog threshold.** The watchdog now reads its own `compaction_log.jsonl` and fires just below where auto-compaction is *actually* occurring lately (auto-trigger events only, recent history, matched by window class), instead of a fixed fraction of the window. This tracks drift in Claude Code's compaction point — which was observed to move ~70k tokens in two weeks — so the threshold no longer silently goes stale and gets preempted. Falls back to the fixed `WATCHDOG_PCT_*` fraction until a project has logged enough auto-compactions; disable with `NOTES_WATCHDOG_AUTOCAL=0`. New tunables: `AUTOCAL_LOOKBACK_DAYS`, `AUTOCAL_MIN_SAMPLES`, `AUTOCAL_ANCHOR_PCT`, `AUTOCAL_RUNWAY`.

### Changed
- The watchdog fire banner now reports the calibrated compaction estimate (and whether it's calibrated from history or falling back to the window cap).

## [1.1.0] - 2026-06-25

### Changed
- **Relicensed from MIT to the Apache License 2.0.** Same permissive, commercial-friendly terms, now with an explicit attribution channel: a `NOTICE` file that redistributors must retain (Apache 2.0 §4(d)), so credit travels with the code.

### Added
- `NOTICE` file carrying project attribution (Jesse Stormer / Stormer Creative; built with Claude Code).
- **Credits** section in the README.

## [1.0.0] - 2026-06-22

Initial release.

### Added
- **Continuity watchdog** (`hooks/continuity_watchdog.py`) — a Stop / PreCompact / SessionStart hook that forces a full notes update before Claude Code auto-compacts a long session. Includes per-model context-window detection (empirical + name heuristic), a fire/re-arm cycle, per-project state, and a loop guard.
- **Local-time injector** (`hooks/inject_local_time.py`) — a UserPromptSubmit hook that injects the real **local** date/time into context so Claude never relies on its drift-prone date sense. Throttled: fires on session start, on a date rollover, else at most every ~2h.
- **`/update-notes` command** (`commands/update-notes.md`) — run the same thorough notes pass on demand.
- `settings.snippet.json` — copy-paste hook registration for `settings.json`.
- README (install for macOS/Linux/Windows, customization, repurpose ideas), MIT license, contributing guide.

### Configuration (environment overrides)
- `CONTINUITY_WATCHDOG_ROOT` — confine the watchdog to one project tree (default: active everywhere).
- `NOTES_WATCHDOG_PCT` / `NOTES_WATCHDOG_TOKENS` — override the fire threshold.
- `NOTES_WATCHDOG_REARM_PCT` / `NOTES_WATCHDOG_REARM_TOKENS` — override the re-arm floor (set tokens to `0` to allow an immediate test fire).
- `LOCAL_TIME_REFRESH_SECONDS` — how often the date hook re-injects (default 7200).
