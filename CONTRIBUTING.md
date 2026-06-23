# Contributing

Thanks for wanting to make the Claude Continuity Kit better. It exists to be shared and improved by the people who use it, so issues and pull requests are genuinely welcome — including "here's a rough idea" issues.

## Ways to help

- **Report a problem** — open an issue with your OS, Python version, Claude Code version, and what happened vs. what you expected. Paste the relevant `settings.json` hook block and any output.
- **Suggest an improvement** — open an issue describing the use case. New `build_notes_directive()` recipes, additional context injectors, and portability fixes are all fair game.
- **Send a pull request** — small, focused PRs are easiest to review.

## The one hard rule

**A hook must never break a Claude Code session.** Concretely:

- Every entry point ends in `exit 0`, even on error.
- All file I/O and parsing is wrapped in `try/except`.
- No network calls and no third-party dependencies — **Python standard library only**.
- Cross-platform by default: assume macOS, Linux, and Windows users.

If a change can't hold those guarantees, it probably belongs behind an opt-in env var rather than in the default path.

## Testing your change

No framework needed — these are small scripts you can exercise directly.

**Date hook** (prints today's local date on first run, then stays silent on an immediate re-run — that's the throttle):

```bash
echo '{}' | python3 hooks/inject_local_time.py
```

**Watchdog** — force a cold fire with a synthetic transcript. Create a one-line JSONL file containing an assistant `usage` block, then pipe a `Stop` event at it with the re-arm floor dropped:

```bash
printf '%s\n' '{"message":{"usage":{"input_tokens":5000},"model":"claude-haiku-4-5"}}' > /tmp/t.jsonl
echo '{"hook_event_name":"Stop","session_id":"t","transcript_path":"/tmp/t.jsonl","cwd":"/tmp"}' \
  | NOTES_WATCHDOG_TOKENS=1 NOTES_WATCHDOG_REARM_TOKENS=0 python3 hooks/continuity_watchdog.py
```

You should see a JSON object containing `"decision": "block"`. Confirm every invocation exits 0 (`echo $?`).

## Style

Match the existing code: comments that explain **why**, descriptive names, standard library only. If you add a knob or env var, keep the README and `settings.snippet.json` in sync with it.
