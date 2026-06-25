# Claude Continuity Kit

> **Stop Claude Code from losing your work to a context reset — and from stamping the wrong date on your notes.**

![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)
&nbsp;![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-blue)
&nbsp;![Python](https://img.shields.io/badge/python-3.x-blue)
&nbsp;![Dependencies](https://img.shields.io/badge/dependencies-none%20%28stdlib%29-brightgreen)

Three small, independent pieces that stop Claude Code from **losing your work to a context reset** and from **stamping the wrong date** on your notes. Drop them into `~/.claude/`, add a few lines to `settings.json`, done.

Built and battle-tested in a real Claude Code workflow. Every component is fail-safe: if anything goes wrong, the hook exits cleanly and your session is never broken.

---

## The problem it solves

Long Claude Code sessions hit **auto-compaction** — the conversation gets summarized to fit the context window, and the fine detail (decisions, gotchas, half-finished work) is gone. You often don't notice until the next session, when Claude has amnesia about what it was doing.

On top of that, Claude's sense of **"today's date"** drifts: the date is injected once at session start, so in a long/resumed session it goes stale, and in the evening it can be a full day off (UTC vs local). Result: notes and commits stamped with the wrong date.

This kit fixes both:

| Component | Type | What it does |
|---|---|---|
| **`continuity_watchdog.py`** | Stop / PreCompact / SessionStart hook | Right before the window fills, **blocks the turn** and forces Claude to update your project notes while the detail still exists. Logs compactions and reminds the next session to verify its notes. |
| **`inject_local_time.py`** | UserPromptSubmit hook | Injects the **real local date/time** into context — throttled (on session start, on a date rollover, else ~every 2h; **not** every message) — so Claude never guesses the date. |
| **`update-notes.md`** | Slash command (`/update-notes`) | Run the same thorough notes pass **on demand**, any time. |

Use all three, or just the one you want — they're independent.

---

## What's in the box

```
Claude Continuity Kit/
├── README.md                  <- you are here
├── hooks/
│   ├── continuity_watchdog.py <- the watchdog (3 events)
│   └── inject_local_time.py   <- the live-date injector
├── commands/
│   └── update-notes.md        <- the /update-notes slash command
└── settings.snippet.json      <- the hooks block to merge into settings.json
```

---

## Install (5 minutes)

**Requirements:** Claude Code, and Python 3 on your PATH (check `python3 --version` — on Windows, use `python --version`; `python3` there is often a non-working Microsoft Store stub).

### 1. Copy the files into `~/.claude/`

```bash
# macOS / Linux — run these from INSIDE the unzipped kit folder
cd "/path/to/Claude Continuity Kit"
mkdir -p ~/.claude/hooks ~/.claude/commands
cp hooks/*.py        ~/.claude/hooks/
cp commands/*.md     ~/.claude/commands/
```

```powershell
# Windows (PowerShell) — run these from INSIDE the unzipped kit folder
cd "C:\path\to\Claude Continuity Kit"
mkdir "$env:USERPROFILE\.claude\hooks" -Force
mkdir "$env:USERPROFILE\.claude\commands" -Force
copy hooks\*.py     "$env:USERPROFILE\.claude\hooks\"
copy commands\*.md  "$env:USERPROFILE\.claude\commands\"
```

### 2. Register the hooks in `~/.claude/settings.json`

Open `~/.claude/settings.json` and merge in the `hooks` block from `settings.snippet.json`.
If you already have a `"hooks"` key, add these four event arrays **into** it — don't replace the whole object. **Do not copy the `_comment` line** from the snippet into your real `settings.json` — it's documentation only.

**macOS / Linux** — use the snippet as-is (`python3 ~/.claude/hooks/...`).

**Windows** — use full paths and `python`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python \"C:\\Users\\YOU\\.claude\\hooks\\inject_local_time.py\"", "timeout": 5 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python \"C:\\Users\\YOU\\.claude\\hooks\\continuity_watchdog.py\"", "timeout": 10 } ] }
    ],
    "PreCompact": [
      { "matcher": "", "hooks": [ { "type": "command", "command": "python \"C:\\Users\\YOU\\.claude\\hooks\\continuity_watchdog.py\"", "timeout": 10 } ] }
    ],
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "python \"C:\\Users\\YOU\\.claude\\hooks\\continuity_watchdog.py\"", "timeout": 5 } ] }
    ]
  }
}
```

> Replace `YOU` with your Windows username — but **keep the double backslashes** (`\\`); a single `\` is an invalid JSON escape and will break the file. On Windows use `python` (not `python3`); on macOS/Linux use `python3`. Check with `python --version` / `python3 --version`.

### 3. Start a NEW Claude Code session

You **must** start a fresh session after editing `settings.json` — a session that's already open will **not** reload hook config, so testing in it will look like the install failed.

### 4. Verify

- **Date hook** — run the script directly; it prints today's local date on the first run, then stays silent on an immediate re-run (that's the throttle working):
  - macOS/Linux: `echo '{}' | python3 ~/.claude/hooks/inject_local_time.py`
  - Windows (PowerShell): `'{}' | python "$env:USERPROFILE\.claude\hooks\inject_local_time.py"`

  In a live session the `[REAL LOCAL CLOCK ...]` line appears on the first prompt (and again after a midnight rollover / every ~2h — throttled, not every message).
- **Watchdog** — it only fires once a session is **already past ~50% of the model's window** (the re-arm floor), so you can't demo it on a brand-new chat by lowering the fire threshold alone. To force a fire cold, drop BOTH the fire threshold and the re-arm floor for one session:
  - macOS/Linux: `NOTES_WATCHDOG_TOKENS=1 NOTES_WATCHDOG_REARM_TOKENS=0 claude` — the next Stop triggers the notes pass.
  - Windows (PowerShell): `$env:NOTES_WATCHDOG_TOKENS=1; $env:NOTES_WATCHDOG_REARM_TOKENS=0; claude` (later unset with `Remove-Item Env:NOTES_WATCHDOG_TOKENS,Env:NOTES_WATCHDOG_REARM_TOKENS`).
- **Command:** type `/update-notes` in any project.

---

## Customize (the knobs)

All in the **TUNABLES** block at the top of `continuity_watchdog.py`:

- **Where it runs** — `WATCHDOG_ROOT`: empty = every project (default); set a path to confine it to one tree. (Env: `CONTINUITY_WATCHDOG_ROOT`.)
- **When it fires** — `WATCHDOG_PCT_1M` / `WATCHDOG_PCT_200K` (fraction of the window). Or force it: env `NOTES_WATCHDOG_PCT` (a fraction) or `NOTES_WATCHDOG_TOKENS` (an absolute count).
- **Re-arm floor** — `REARM_PCT` (0.50): after firing, the watchdog stays silent — and won't fire at all — until context drops below this fraction of the window. This is why a fresh, low-token chat can't trigger it even with `NOTES_WATCHDOG_TOKENS=1`. Override with env `NOTES_WATCHDOG_REARM_PCT`, or `NOTES_WATCHDOG_REARM_TOKENS` (set to `0` to allow an immediate test fire).
- **Model windows** — `window_for_model()`: edit the name list / sizes if your models differ.
- **What notes get written** — `build_notes_directive()`: this is just text. Rewrite it to match YOUR doc names and standards (it's the easiest thing to repurpose).

The date hook is throttled (session-start + date-change + every ~2h). Tune the interval with the `LOCAL_TIME_REFRESH_SECONDS` env var (e.g. `0` to inject every turn, `21600` for 6h). To restrict it to one project, gate on `os.getcwd()` at the top of `inject_local_time.py`.

---

## Repurpose ideas

The `build_notes_directive()` function is plain text Claude is forced to act on — swap it for anything you want done before a context reset:

- **Different docs** — point it at `ARCHITECTURE.md`, `DECISIONS.md`, a daily journal, etc.
- **Commit instead** — force a `git add -A && git commit` checkpoint before compaction.
- **Export state** — write a machine-readable `state.json` snapshot for tooling.
- **Team handoff** — generate a standup-style summary into a shared file.

The watchdog's real trick is the **Stop-hook block**: any "do X before the turn ends, no matter what" behavior can be built the same way (`{"decision":"block","reason":"...do X..."}`). The `inject_local_time.py` pattern (UserPromptSubmit stdout -> context) is the template for **injecting any fresh fact every turn** — git branch, current sprint, an API status, on-call rotation, etc.

---

## Safety & uninstall

- **Never breaks a session.** Every hook is wrapped to exit 0 on any error. Worst case, it does nothing.
- **No network, no external deps.** Pure Python standard library. Reads only your local transcript; writes only to each project's `.claude/state/`.
- **Loop-safe.** The watchdog won't re-block a stop it triggered (`stop_hook_active` guard) and fires once per fill-cycle.
- **Uninstall:** delete the four event entries from `settings.json` (and optionally the files from `~/.claude/hooks` and `~/.claude/commands`). Per-project `.claude/state/` folders can be removed any time.

---

## Contributing

Improvements, bug reports, and rough ideas are all welcome — this is meant to be shared and made better by the people who use it. Open an issue for anything you hit, or send a PR. See **[CONTRIBUTING.md](CONTRIBUTING.md)**.

The only hard rule: **a hook must never break a session** — every path exits 0, all I/O is wrapped, and it stays pure-stdlib with no network calls. Keep it that way and almost anything goes.

## Credits

Researched, designed, and battle-tested by **[Jesse Stormer](https://github.com/jessestormer)** (Stormer Creative), built with **Claude Code** (Anthropic). Every piece came out of real long-session debugging — diagnosing a wrong-date bug and stress-testing what actually survives a context reset — not a weekend hack.

Using it, forking it, or building on it? A credit and a link back to this repo is genuinely appreciated. The [`NOTICE`](NOTICE) file carries the attribution the Apache License asks redistributors to keep.

## License

**[Apache License 2.0](LICENSE)** © 2026 Jesse Stormer. Use it, fork it, ship it commercially — just keep the [`NOTICE`](NOTICE) file and the attribution it carries. That's the Apache 2.0 ask, and it's how credit travels with the code.

---

*Pure-stdlib Python, cross-platform, fail-safe. No warranty — but it's hard for it to do harm, since it can only ever add context or ask Claude to write notes.*
