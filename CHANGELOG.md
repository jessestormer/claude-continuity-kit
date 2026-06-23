# Changelog

All notable changes to the Claude Continuity Kit are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

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
