#!/usr/bin/env python3
"""
continuity_watchdog.py  -  "before the context window fills and resets, write the notes"

A single hook script wired to three events for Claude Code and Codex. Its job:
stop valuable session detail from vanishing when the agent auto-compacts
(summarizes) a long conversation. Right before the window fills, it FORCES the
agent to bring the project's continuity docs (DEV_NOTES / CHANGELOG / LESSONS /
TODO) fully up to date while the detail still exists.

  Stop         -> WATCHDOG. When context-in-use crosses a fraction of the model's
                  window, it BLOCKS the stop and makes the agent update the notes
                  before it is allowed to finish the turn.
  PreCompact   -> BACKSTOP. Fires right before a compaction; it can't make the agent
                  work (the turn is over), so it logs the real compaction size and
                  drops a per-session "verify your notes" flag for the next start.
  SessionStart -> If this session was just resumed/compacted, injects a reminder
                  to verify/finish the notes before continuing.

WHY IT WORKS: the agent cannot reliably see how close it is to auto-compaction,
and its own sense of "I should write notes now" is unreliable. A Stop hook CAN
read the real token count from the transcript and block the turn, turning
"please remember to write notes" into something the harness enforces, not
something left to the model's judgement.

DESIGN RULES
  * NEVER breaks a session: any error -> exit 0, no block.
  * Fires ONCE per fill-cycle; re-arms after a compaction drops context back down.
  * Optional gating (see WATCHDOG_ROOT): register it globally but, if you want,
    only have it act inside one project tree.

See README.md for install. Edit the TUNABLES block below to taste.
"""

import json
import os
import shutil
import sys
import datetime

# ===========================================================================
# TUNABLES — the knobs you are most likely to change
# ===========================================================================

# WHERE IT IS ACTIVE.
#   ""  (empty)      -> active in EVERY project (recommended default).
#   "/path/to/proj"  -> active ONLY inside that folder tree (handy if you register
#                       this globally but only want it in one workspace).
# Env override: CONTINUITY_WATCHDOG_ROOT
WATCHDOG_ROOT = os.environ.get("CONTINUITY_WATCHDOG_ROOT", "")

# CONTEXT WINDOW per model. Claude Code's transcript logs only the bare model name
# (no window size), so we resolve the window two ways:
#   1) EMPIRICAL (reliable): if a usage block ON THIS MODEL ever exceeded 210k
#      tokens, its window is provably 1M (a call can't exceed its own cap).
#   2) NAME heuristic (fallback): Opus / Sonnet / Fable -> 1M, else 200k.
# Adjust the name list below if your models differ.
def window_for_model(model: str, observed_used: int = 0) -> int:
    if observed_used and observed_used > 210_000:
        return 1_000_000
    m = (model or "").lower()
    if "opus" in m or "sonnet" in m or "fable" in m:
        return 1_000_000
    return 200_000

# TRIGGER FRACTION per window class. Auto-compaction fires near the FULL window,
# so leave enough headroom to actually write the notes (~40-50k tokens):
#   1M window   -> 0.95  (fire ~950k, ~50k of room)
#   200k window -> 0.80  (fire ~160k, ~40k of room; 0.95 would leave too little)
WATCHDOG_PCT_1M   = 0.95
WATCHDOG_PCT_200K = 0.80
REARM_PCT         = 0.50   # re-arm once context drops back below this fraction

# SELF-CALIBRATION (optional, on by default). The token count at which Claude
# Code auto-compacts is NOT fixed — it varies by model and has been observed to
# DRIFT across releases. A hard-coded fraction can end up ABOVE where compaction
# actually fires, which silently preempts the watchdog (it never gets to block).
# So instead of trusting a fixed fraction, read this project's OWN compaction log
# and aim just below where auto-compaction is firing lately. Uses trigger==auto
# rows only (a manual /compact fires at an arbitrary fill and is not the cap),
# the same window class as the current model, and only recent history (so it
# follows drift). Falls back to the fixed fraction until there's enough data
# (a fresh install has none). Disable with env NOTES_WATCHDOG_AUTOCAL=0.
AUTOCAL_LOOKBACK_DAYS = 14      # only consider auto-compactions from the last N days
AUTOCAL_MIN_SAMPLES   = 3       # need at least this many recent points before trusting the estimate
AUTOCAL_ANCHOR_PCT    = 0.15    # anchor at the 15th percentile of recent points (robust to a lone low outlier)
AUTOCAL_RUNWAY        = 50_000  # fire this far below the anchor, leaving room to finish the notes pass

# Env overrides: NOTES_WATCHDOG_PCT (one fraction for ALL windows),
#                NOTES_WATCHDOG_TOKENS (absolute fire threshold for ALL models),
#                NOTES_WATCHDOG_AUTOCAL=0 (turn OFF self-calibration; use the fixed fraction),
#                NOTES_WATCHDOG_REARM_PCT / NOTES_WATCHDOG_REARM_TOKENS (the re-arm
#                floor; set NOTES_WATCHDOG_REARM_TOKENS=0 so a fresh, low-token
#                session can fire immediately — useful for testing the watchdog).


def watchdog_pct(window: int) -> float:
    penv = os.environ.get("NOTES_WATCHDOG_PCT")
    if penv:
        try:
            return float(penv)
        except ValueError:
            pass
    return WATCHDOG_PCT_1M if window >= 1_000_000 else WATCHDOG_PCT_200K


def _autocal_off() -> bool:
    return os.environ.get("NOTES_WATCHDOG_AUTOCAL", "1").strip().lower() in ("0", "false", "no")


def _percentile(sorted_vals: list, pct: float) -> int:
    if not sorted_vals:
        return 0
    return sorted_vals[int(pct * (len(sorted_vals) - 1))]


def _compaction_log_path(runtime: str = "claude") -> str:
    """Return the runtime-specific compaction ledger path."""
    name = "compaction_log.codex.jsonl" if runtime == "codex" else "compaction_log.jsonl"
    return os.path.join(STATE_DIR, name)


def _same_calibration_window(requested_window: int, logged_window: int, runtime: str) -> bool:
    if runtime == "codex":
        return int(logged_window or 0) == int(requested_window or 0)
    return (logged_window >= 1_000_000) == (requested_window >= 1_000_000)


def _recent_auto_points(window: int, runtime: str = "claude") -> list:
    """used_tokens of recent AUTO compactions for this runtime/window.

    Claude keeps the original window-class calibration behavior. Codex uses its
    own physical ledger and requires an exact observed-window match because Codex
    reports the context window directly in token_count events.
    """
    try:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=AUTOCAL_LOOKBACK_DAYS)
    except Exception:
        return []
    pts = []
    try:
        with open(_compaction_log_path(runtime), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                except Exception:
                    continue
                if c.get("trigger") != "auto":
                    continue
                row_runtime = c.get("runtime") or ("codex" if runtime == "codex" else "claude")
                if row_runtime != runtime:
                    continue
                used = c.get("used_tokens")
                if not isinstance(used, int) or used <= 0:   # skip synthetic/placeholder rows
                    continue
                raw_window = c.get("window")
                try:
                    logged_window = int(raw_window) if raw_window else window_for_model(c.get("model") or "", used)
                except Exception:
                    logged_window = window_for_model(c.get("model") or "", used)
                if not _same_calibration_window(window, logged_window, runtime):
                    continue
                try:
                    when = datetime.datetime.fromisoformat(c.get("ts") or "")
                except Exception:
                    continue
                if when >= cutoff:
                    pts.append(used)
    except Exception:
        return []
    return pts


def compaction_estimate(window: int, runtime: str = "claude"):
    """Where auto-compaction CURRENTLY fires for this window/runtime, from recent
    history — or None when there isn't enough recent data to say."""
    if _autocal_off():
        return None
    pts = _recent_auto_points(window, runtime)
    if len(pts) < AUTOCAL_MIN_SAMPLES:
        return None
    pts.sort()
    return _percentile(pts, AUTOCAL_ANCHOR_PCT)


def watchdog_tokens(model: str = "", window: int = None, runtime: str = "claude") -> int:
    env = os.environ.get("NOTES_WATCHDOG_TOKENS")   # absolute override (used by tests)
    if env and env.strip().isdigit():
        return int(env.strip())
    if window is None:
        window = window_for_model(model)
    if os.environ.get("NOTES_WATCHDOG_PCT"):        # explicit fraction override beats calibration
        return int(window * watchdog_pct(window))
    est = compaction_estimate(window, runtime)      # data-driven: aim just below the real compaction point
    if est is not None:
        thresh = est - AUTOCAL_RUNWAY
        return max(int(window * 0.50), min(thresh, int(window * 0.95)))   # sanity clamp
    return int(window * watchdog_pct(window))       # fallback: fixed fraction (cold start, no data)


def rearm_tokens(model: str = "", window: int = None) -> int:
    env = os.environ.get("NOTES_WATCHDOG_REARM_TOKENS")
    if env and env.strip().isdigit():          # absolute floor (0 = no floor)
        return int(env.strip())
    if window is None:
        window = window_for_model(model)
    penv = os.environ.get("NOTES_WATCHDOG_REARM_PCT")
    if penv:
        try:
            return int(window * float(penv))
        except ValueError:
            pass
    return int(window * REARM_PCT)


# State dir (re-arm flags, compaction log, fire log). Set in main(). By default
# state is per-project (<cwd>/.claude/state). If WATCHDOG_ROOT is configured,
# state follows that root so one workspace can share calibration across projects.
STATE_DIR = ""

# Codex-specific continuity snapshots. Codex PreCompact cannot rely on visible
# stdout, so the hook writes a durable project-local snapshot before compaction.
CODEX_CONTINUITY_SUBDIR = os.path.join(".codex", "continuity")
CODEX_SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024
CODEX_SNAPSHOT_MAX_MESSAGES = 40
CODEX_SNAPSHOT_MAX_TOOLS = 40
CODEX_SNAPSHOT_TEXT_LIMIT = 120_000
CODEX_FIELD_TEXT_LIMIT = 6_000


# ===========================================================================
# The instruction Claude is FORCED to act on when the watchdog trips.
# This is the heart of the feature — keep it demanding and concrete.
# Customize the doc set / standards to match how you keep notes.
# ===========================================================================
def build_notes_directive(today: str) -> str:
    """`today` is the REAL local system date, injected so notes are dated
    correctly — a model's own date sense drifts, especially after a compaction."""
    return (
        "[SESSION-CONTINUITY WATCHDOG] The context window is about to fill and reset, "
        "which will erase the fine detail of this session. STOP working on the current "
        "task and, FIRST, bring this project's continuity docs fully up to date. Do not "
        "continue the original task until this is done.\n"
        "\n"
        f"TODAY IS {today}. Use THIS date for every stamp and entry below — do NOT trust "
        "your own sense of the date, it drifts (especially after a compaction).\n"
        "\n"
        "In the CURRENT project folder (project root AND any `memory/` subfolder — match "
        "where these already live; create if missing). These docs have DIFFERENT jobs; "
        "keeping them distinct is what stops OLD notes from bleeding into what reads as "
        "'current state' — the #1 way continuity notes mislead a fresh reader:\n"
        "\n"
        f"  - DEV_NOTES.md = CURRENT STATE ONLY — a LIVING doc you OVERWRITE in place. Put "
        f"`_Last updated: {today}_` at the top. BEFORE adding anything, RE-READ what's there "
        "and DELETE or CORRECT every line that is no longer true — never leave a stale "
        "'current state' sitting beside the new one. This file is NOT an archive: if a "
        "superseded fact is worth keeping, move it to CHANGELOG, don't keep it here. Cover "
        "architecture, what's in progress, key decisions + WHY, gotchas, the file/module map, "
        f"and how to build/run/test. If you can't verify an existing line, mark it "
        f"`[stale? unverified {today}]` rather than letting it read as fact.\n"
        f"  - CHANGELOG.md = DATED HISTORY — append-only, newest on top. Add a `## {today}` "
        "entry for what actually changed this session (files, behavior, IDs, config). This is "
        "WHERE old state belongs, so it stops contaminating DEV_NOTES.\n"
        f"  - LESSONS.md = ACCUMULATED GOTCHAS — append-only, NEVER pruned (a growing "
        "knowledge base, separate from CHANGELOG). If a non-trivial bug was resolved this "
        f"session, add a dated entry: `## [{today}] <short title>`, then `Symptom:` / "
        "`Root cause:` / `Fix:` / `Prevent:`. This is what lets a future session avoid "
        "re-hitting the same bug and trace 'X broke Y' causation.\n"
        f"  - TODO.md = OPEN ITEMS ONLY. Stamp new items `({today})`. Check off or DELETE "
        "anything now done or obsolete. Give the precise next step + blockers, actionable cold.\n"
        "  - Any other notes that drifted (README, relevant memory/ files).\n"
        "\n"
        "WRITING STANDARD: assume the reader is a brand-new person — or a fresh Claude with "
        "zero memory of this conversation — who must resume tomorrow and know EXACTLY what is "
        "true NOW. Be specific: real file paths, commands, IDs, decisions and their rationale, "
        "what works, what's half-finished, what's next. State facts only — never invent status "
        "you haven't verified.\n"
        "\n"
        "When the notes are complete and saved, you may resume the original task."
    )


# ===========================================================================
# helpers
# ===========================================================================

def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _session_cwd(data: dict) -> str:
    return (
        data.get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("CODEX_PROJECT_DIR")
        or os.getcwd()
        or ""
    )


def _active_here(path: str) -> bool:
    """True if the watchdog should act for this session's cwd."""
    if not WATCHDOG_ROOT:
        return True
    try:
        root = os.path.normcase(os.path.abspath(WATCHDOG_ROOT))
        cur = os.path.normcase(os.path.abspath(path))
        return cur == root or cur.startswith(root + os.sep)
    except Exception:
        return False


def _tail_bytes(path: str, max_bytes: int = 512 * 1024) -> str:
    """Read only the tail of a (potentially huge) transcript, fast."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        start = max(0, size - max_bytes)
        fh.seek(start)
        data = fh.read()
    if start > 0:  # drop a possibly-partial first line
        nl = data.find(b"\n")
        if nl != -1:
            data = data[nl + 1:]
    return data.decode("utf-8", errors="replace")


def _is_codex_sessions_path(path: str) -> bool:
    norm = str(path or "").replace("\\", "/").lower()
    return "/.codex/sessions/" in norm or norm.endswith("/.codex/sessions")


def _transcript_has_codex_token_count(transcript_path: str) -> bool:
    if not transcript_path or not os.path.isfile(transcript_path):
        return False
    try:
        for line in _tail_bytes(transcript_path, 256 * 1024).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            payload = ev.get("payload") if isinstance(ev, dict) else None
            if isinstance(payload, dict) and payload.get("type") == "token_count":
                return True
    except Exception:
        return False
    return False


def _is_codex_payload(data: dict) -> bool:
    """Return True only on Codex-specific evidence.

    Do not key on generic fields like `model`, and do not trust CODEX_HOME alone;
    either can exist in another runtime. The Codex side of the boundary is the
    transcript path or Codex token-count telemetry.
    """
    transcript = data.get("transcript_path") or ""
    return _is_codex_sessions_path(transcript) or _transcript_has_codex_token_count(transcript)


def _int_value(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _codex_usage_total(usage: dict) -> int:
    if not isinstance(usage, dict):
        return 0
    total = _int_value(usage.get("total_tokens"))
    if total:
        return total
    return _int_value(usage.get("input_tokens")) + _int_value(usage.get("output_tokens"))


def _claude_usage_total(usage: dict) -> int:
    if not isinstance(usage, dict):
        return 0
    return (
        _int_value(usage.get("input_tokens"))
        + _int_value(usage.get("cache_read_input_tokens"))
        + _int_value(usage.get("cache_creation_input_tokens"))
        + _int_value(usage.get("output_tokens"))
    )


def _usage_summary(transcript_path: str, model_hint: str = ""):
    """Return (last_used, peak_on_current_model, current_model, observed_window).

    Claude transcripts expose assistant message usage blocks. Codex transcripts
    expose token_count events whose last_token_usage is the active context-sized
    request; total_token_usage is cumulative and can exceed the context window.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None, None, model_hint or None, None

    claude_samples = []   # (used, model) per assistant usage block, in order
    codex_samples = []    # (used, window) per token_count event, in order

    for line in _tail_bytes(transcript_path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue

        payload = ev.get("payload") if isinstance(ev, dict) else None
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
            used = _codex_usage_total(usage)
            if used:
                codex_samples.append((used, _int_value(info.get("model_context_window"))))
            continue

        msg = ev.get("message") if isinstance(ev, dict) else None
        if isinstance(msg, dict):
            u = msg.get("usage")
            if isinstance(u, dict):
                used = _claude_usage_total(u)
                claude_samples.append((used, msg.get("model") or model_hint))

    if codex_samples:
        last_used, observed_window = codex_samples[-1]
        peak_current = max((used for used, _window in codex_samples), default=last_used)
        return last_used, peak_current, model_hint or None, observed_window or None

    if not claude_samples:
        return None, None, model_hint or None, None

    last_used, current_model = claude_samples[-1]
    peak_current = max(
        (used for used, m in claude_samples if m == current_model or m is None),
        default=last_used,
    )
    return last_used, peak_current, current_model or model_hint, None


def _slug(value: str, fallback: str = "unknown") -> str:
    safe = "".join(c for c in (value or fallback) if c.isalnum() or c in "-_")
    return safe or fallback


def _clip(text: str, limit: int = CODEX_FIELD_TEXT_LIMIT) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 80)].rstrip() + "\n...[truncated by continuity hook]"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    parts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
    elif isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(p for p in parts if p).strip()


def _codex_continuity_dir(data: dict) -> str:
    cwd = _session_cwd(data)
    if not _active_here(cwd):
        return ""
    return os.path.join(cwd, CODEX_CONTINUITY_SUBDIR)


def _extract_codex_snapshot(transcript_path: str) -> dict:
    messages = []
    tools = []
    token_info = {}
    if not transcript_path or not os.path.isfile(transcript_path):
        return {"messages": messages, "tools": tools, "token_info": token_info}

    for line in _tail_bytes(transcript_path, CODEX_SNAPSHOT_MAX_BYTES).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        payload = ev.get("payload") if isinstance(ev, dict) else None
        if not isinstance(payload, dict):
            continue

        ts = ev.get("timestamp") or ""
        ev_type = ev.get("type")
        payload_type = payload.get("type")

        if ev_type == "response_item" and payload_type == "message":
            role = payload.get("role")
            if role in ("user", "assistant"):
                text = _content_text(payload.get("content"))
                if text:
                    messages.append({"role": role, "text": text, "ts": ts})
        elif ev_type == "event_msg" and payload_type in ("user_message", "agent_message"):
            msg = payload.get("message")
            if isinstance(msg, str) and msg.strip():
                role = "assistant" if payload_type == "agent_message" else "user"
                messages.append({"role": role, "text": msg, "ts": ts})
        elif ev_type == "response_item" and payload_type == "function_call":
            tools.append({
                "name": payload.get("name") or "tool_call",
                "args": _clip(payload.get("arguments") or "", 2_000),
                "ts": ts,
            })
        elif ev_type == "event_msg" and payload_type == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            token_info = {
                "last_token_usage": info.get("last_token_usage"),
                "model_context_window": info.get("model_context_window"),
            }

    return {
        "messages": messages[-CODEX_SNAPSHOT_MAX_MESSAGES:],
        "tools": tools[-CODEX_SNAPSHOT_MAX_TOOLS:],
        "token_info": token_info,
    }


def _latest_codex_snapshot_path(data: dict) -> str:
    outdir = _codex_continuity_dir(data)
    if not outdir:
        return ""
    latest = os.path.join(outdir, "latest.md")
    return latest if os.path.isfile(latest) else ""


def _write_codex_precompact_snapshot(data: dict, used: int = None, window: int = None, model: str = "") -> str:
    if not _is_codex_payload(data):
        return ""
    outdir = _codex_continuity_dir(data)
    if not outdir:
        return ""

    digest = _extract_codex_snapshot(data.get("transcript_path") or "")
    session_id = _slug(data.get("session_id") or "unknown")
    turn_id = _slug(data.get("turn_id") or "turn")
    trigger = data.get("trigger") or "unknown"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(outdir, f"{stamp}_{session_id}_{turn_id}_{trigger}.md")
    latest = os.path.join(outdir, "latest.md")

    lines = [
        "# Codex Continuity Snapshot",
        "",
        f"_Generated: {_now()}_",
        "",
        "## Event",
        f"- Event: {data.get('hook_event_name') or 'PreCompact'}",
        f"- Trigger: {trigger}",
        f"- Session: {data.get('session_id') or 'unknown'}",
        f"- Turn: {data.get('turn_id') or 'unknown'}",
        f"- Model: {model or data.get('model') or 'unknown'}",
        f"- CWD: {_session_cwd(data)}",
    ]
    if used and window:
        pct = int(round(100.0 * used / window)) if window else 0
        lines.append(f"- Context estimate: {used:,} / {window:,} tokens ({pct}%)")
    elif used:
        lines.append(f"- Context estimate: {used:,} tokens")
    if data.get("transcript_path"):
        lines.append(f"- Transcript: {data.get('transcript_path')}")

    lines.extend(["", "## Recent Conversation"])
    if digest["messages"]:
        for msg in digest["messages"]:
            role = (msg.get("role") or "message").title()
            ts = msg.get("ts") or ""
            lines.extend(["", f"### {role} {ts}".rstrip(), _clip(msg.get("text") or "")])
    else:
        lines.append("No plain-text user/assistant message content was available in the transcript tail.")

    lines.extend(["", "## Recent Tool Calls"])
    if digest["tools"]:
        for tool in digest["tools"]:
            lines.extend([
                "",
                f"### {tool.get('name') or 'tool_call'} {tool.get('ts') or ''}".rstrip(),
                "```json",
                _clip(tool.get("args") or "", 2_000),
                "```",
            ])
    else:
        lines.append("No recent tool calls were available in the transcript tail.")

    lines.extend([
        "",
        "## Resume Checklist",
        "- Read this snapshot before relying on the compacted conversation summary.",
        "- Reconcile project DEV_NOTES.md, CHANGELOG.md, LESSONS.md, and TODO.md before continuing substantial work.",
        "- Treat the original transcript as the source of truth if this snapshot and the compacted summary disagree.",
    ])

    body = "\n".join(lines).strip() + "\n"
    if len(body) > CODEX_SNAPSHOT_TEXT_LIMIT:
        body = body[:CODEX_SNAPSHOT_TEXT_LIMIT].rstrip() + "\n\n...[snapshot truncated by continuity hook]\n"

    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    shutil.copyfile(path, latest)
    return path


def _state_path(session_id: str) -> str:
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")
    return os.path.join(STATE_DIR, f"watchdog_{safe or 'unknown'}.json")


def _load_state(session_id: str) -> dict:
    try:
        with open(_state_path(session_id), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(session_id: str, state: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(_state_path(session_id), "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _now() -> str:
    # LOCAL time (never UTC), so dates/stamps match the user's wall clock.
    return datetime.datetime.now().isoformat(timespec="seconds")


def _log_compaction(record: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        runtime = record.get("runtime") or "claude"
        with open(_compaction_log_path(runtime), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _log_fire(record: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "watchdog_fires.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _project_name(data: dict) -> str:
    return os.path.basename(_session_cwd(data).rstrip("/\\")) or "project"


def _pending_flag_path(session_id: str) -> str:
    # PER-SESSION flag: a shared flag would let one session's compaction trip
    # another session's SessionStart reminder.
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")
    return os.path.join(STATE_DIR, "pending_notes_%s.flag" % (safe or "unknown"))


# ===========================================================================
# event handlers
# ===========================================================================

def handle_stop(data: dict) -> None:
    if data.get("stop_hook_active"):   # loop guard: don't re-block our own block
        return

    session_id = data.get("session_id", "")
    runtime = "codex" if _is_codex_payload(data) else "claude"
    used, peak, model, observed_window = _usage_summary(data.get("transcript_path", ""), data.get("model", ""))
    model = data.get("model") or model
    if not used:
        return

    window = observed_window or window_for_model(model, max(used, peak or 0))
    threshold = watchdog_tokens(model, window, runtime)
    state = _load_state(session_id)

    if used < rearm_tokens(model, window):   # re-arm after a compaction
        if state.get("fired"):
            state["fired"] = False
            _save_state(session_id, state)
        return

    if used >= threshold and not state.get("fired"):
        project = _project_name(data)
        pct_now = int(round(100.0 * used / window)) if window else 0
        state.update({
            "fired": True, "fired_at": _now(), "fired_tokens": used,
            "threshold": threshold, "window": window, "model": model,
        })
        _save_state(session_id, state)
        _log_fire({
            "ts": _now(), "session_id": session_id, "project": project,
            "runtime": runtime, "model": model, "used_tokens": used,
            "threshold": threshold, "window": window,
        })
        # The reliable alert channel is the blocked `reason` (shown verbatim as
        # "Stop hook feedback"); `systemMessage` does not always surface. We lead
        # with a loud banner and tell Claude to echo the trigger in its reply.
        est = compaction_estimate(window, runtime)
        compacts_near = est if est is not None else window
        cal_note = "calibrated from recent history" if est is not None else "window cap, no recent data yet"
        banner = (
            "[!] SESSION-CONTINUITY WATCHDOG FIRED\n"
            "   Context: %s / %s tokens  (%d%% full)\n"
            "   Model: %s   Project: [%s]   Threshold: %s\n"
            "   This chat auto-compacts near %s (%s) — writing the notes NOW so nothing is lost.\n"
            "   (Tune NOTES_WATCHDOG_PCT / NOTES_WATCHDOG_TOKENS, or disable auto-cal with NOTES_WATCHDOG_AUTOCAL=0.)\n"
            "------------------------------------------------------------\n\n"
            % (format(used, ","), format(window, ","), pct_now, model or "unknown",
               project, format(threshold, ","), format(compacts_near, ","), cal_note)
        )
        relay = (
            "\n\nBEFORE anything else, open your reply with this exact line so the user is alerted:\n"
            "> [continuity] Watchdog fired at %s/%s tokens (%d%%) — updating the notes before this chat compacts."
            % (format(used, ","), format(window, ","), pct_now)
        )
        print(json.dumps({
            "decision": "block",
            "reason": banner + build_notes_directive(_now().split("T")[0]) + relay,
            "systemMessage": banner.replace("\n", " ").strip(),
        }))


def handle_precompact(data: dict) -> None:
    used, _peak, model, observed_window = _usage_summary(
        data.get("transcript_path", ""), data.get("model", "")
    )
    model = data.get("model") or model
    window = observed_window or (window_for_model(model, used or 0) if (model or used) else None)
    project = _project_name(data)
    session_id = data.get("session_id", "")
    runtime = "codex" if _is_codex_payload(data) else "claude"
    snapshot_path = ""
    if runtime == "codex":
        try:
            snapshot_path = _write_codex_precompact_snapshot(data, used, window, model)
        except Exception:
            snapshot_path = ""

    _log_compaction({
        "ts": _now(), "event": "PreCompact", "trigger": data.get("trigger"),
        "session_id": session_id, "project": project, "model": model,
        "used_tokens": used, "window": window, "cwd": _session_cwd(data),
        "runtime": runtime, "snapshot_path": snapshot_path,
    })
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(_pending_flag_path(session_id), "w", encoding="utf-8") as fh:
            fh.write(_now())
    except Exception:
        pass
    where = (" at ~%s context tokens" % format(used, ",")) if used else ""
    snapshot = (" Snapshot: %s" % snapshot_path) if snapshot_path else ""
    print(json.dumps({
        "systemMessage": "Auto-compaction fired%s in [%s] (%s) — context is being summarized; session detail may be lost.%s"
                         % (where, project, model or "unknown", snapshot),
    }))


def handle_session_start(data: dict) -> None:
    source = (data.get("source") or "").lower()
    flag = _pending_flag_path(data.get("session_id", ""))
    if source not in ("compact", "resume") and not os.path.isfile(flag):
        return
    try:
        if os.path.isfile(flag):
            os.remove(flag)
    except Exception:
        pass
    context = (
        "This session was just compacted or resumed — fine detail from earlier may "
        "have been lost. Before continuing, open this project's DEV_NOTES.md, "
        "CHANGELOG.md, LESSONS.md and TODO.md (project root or memory/) and confirm "
        "they reflect the true current state; finish any notes that were left incomplete."
    )
    latest_snapshot = _latest_codex_snapshot_path(data)
    if latest_snapshot:
        context += " Also read the Codex continuity snapshot at %s." % latest_snapshot
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


# ===========================================================================
# main
# ===========================================================================

def main() -> None:
    data = _read_payload()
    cwd = _session_cwd(data)
    if not _active_here(cwd):     # optional gating; default = active everywhere
        return

    global STATE_DIR
    if WATCHDOG_ROOT:
        base = os.path.abspath(WATCHDOG_ROOT)
    else:
        base = os.path.abspath(cwd) if cwd else os.path.expanduser("~")  # never relative
    STATE_DIR = os.path.join(base, ".claude", "state")

    event = data.get("hook_event_name", "")
    if event == "Stop":
        handle_stop(data)
    elif event == "PreCompact":
        handle_precompact(data)
    elif event == "SessionStart":
        handle_session_start(data)
    # Unknown event -> no-op.


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Absolutely never break a session.
        pass
    sys.exit(0)
