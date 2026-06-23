#!/usr/bin/env python3
"""
inject_local_time.py -- UserPromptSubmit hook (THROTTLED).

Injects the REAL local system date/time into the model's context so Claude never
has to rely on its own sense of "now" -- which drifts after a compaction and goes
stale across a day boundary or in a resumed session.

WHY YOU WANT THIS: Claude Code injects a "Today's date" line once at session
start; in a long, resumed, or compacted session that snapshot goes stale. And a
model asked for the time with no tool will simply guess. The classic symptom is
notes/commits stamped a day off (often because, in the evening, UTC is already
*tomorrow's* date while local is still today). This hook hands Claude the correct
LOCAL date so it never has to choose to look it up.

THROTTLE: UserPromptSubmit fires on EVERY message, which is wasteful for a value
that changes once a day. So it injects only when it matters:
  * the FIRST prompt of a session (no state yet), OR
  * the local DATE changed since last injection (midnight rollover), OR
  * more than REFRESH_SECONDS elapsed since last injection (default 2h; catches
    long sessions and same-day resumes).
Otherwise it exits silently. Per-session throttle state lives in the OS temp dir,
keyed by session_id. Env override: LOCAL_TIME_REFRESH_SECONDS.

CLOCK SOURCE: datetime.now().astimezone() -> LOCAL timezone, never UTC.
CONTRACT:     UserPromptSubmit stdout (exit 0) is appended to the turn's context.
SAFETY:       always exits 0; any error is swallowed so a turn is never broken
              (and on any state error it fails OPEN -> injects, never blocks).

Register in settings.json under "UserPromptSubmit" (see README.md).
"""
import os
import sys
import json
import tempfile
import datetime

# Re-inject at most this often (seconds) -- PLUS always on a date change or the
# first prompt of a session. Default 2 hours.
REFRESH_SECONDS = 7200
try:
    REFRESH_SECONDS = int(os.environ.get("LOCAL_TIME_REFRESH_SECONDS", "") or REFRESH_SECONDS)
except Exception:
    pass


def _read_payload():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _state_path(session_id):
    safe = "".join(c for c in (session_id or "default") if c.isalnum() or c in "-_") or "default"
    return os.path.join(tempfile.gettempdir(), "claude_localtime_%s.json" % safe)


def _should_inject(state_path, now):
    """Inject on first prompt, on a date change, or once per REFRESH window.
    Fails OPEN (returns True) if state can't be read -- correctness over silence."""
    try:
        with open(state_path, encoding="utf-8") as fh:
            st = json.load(fh)
        if now.strftime("%Y-%m-%d") != st.get("date", ""):
            return True
        if (now.timestamp() - float(st.get("ts", 0))) >= REFRESH_SECONDS:
            return True
        return False
    except Exception:
        return True


def main():
    data = _read_payload()
    now = datetime.datetime.now().astimezone()          # LOCAL, tz-aware
    sp = _state_path(data.get("session_id", ""))

    if not _should_inject(sp, now):
        sys.exit(0)                                     # throttled -> silent

    try:
        try:
            utc = now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            off = now.strftime("%z")                     # e.g. -0400
            off = ("UTC%s:%s" % (off[:3], off[3:])) if off else "UTC?"
            tzname = now.tzname() or ""
        except Exception:
            utc, off, tzname = "", "", ""

        msg = (
            "[REAL LOCAL CLOCK -- authoritative. Use this for any date/time stamp; "
            "do NOT trust your own sense of the date, it drifts after compaction and "
            "across day boundaries.] "
            "Local now: %s %s (%s). UTC now: %s. Today's date = %s."
            % (now.strftime("%Y-%m-%d %H:%M:%S %A"), tzname, off, utc,
               now.strftime("%Y-%m-%d"))
        )
        sys.stdout.write(msg + "\n")

        try:                                            # record this injection
            with open(sp, "w", encoding="utf-8") as fh:
                json.dump({"ts": now.timestamp(), "date": now.strftime("%Y-%m-%d")}, fh)
        except Exception:
            pass
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
