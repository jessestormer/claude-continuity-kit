import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

WATCHDOG_ENV_KEYS = (
    "NOTES_WATCHDOG_AUTOCAL",
    "NOTES_WATCHDOG_PCT",
    "NOTES_WATCHDOG_TOKENS",
    "NOTES_WATCHDOG_REARM_PCT",
    "NOTES_WATCHDOG_REARM_TOKENS",
)

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "continuity_watchdog.py"
spec = importlib.util.spec_from_file_location("continuity_watchdog", HOOK)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


@contextlib.contextmanager
def temp_dir():
    base = Path(os.environ.get("AI_CONTINUITY_TEST_TMP") or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    path = base / ("ai-continuity-test-" + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class DualRuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.old_state_dir = watchdog.STATE_DIR
        self.old_watchdog_root = watchdog.WATCHDOG_ROOT
        self.old_env = {key: os.environ.get(key) for key in WATCHDOG_ENV_KEYS}
        for key in WATCHDOG_ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        watchdog.STATE_DIR = self.old_state_dir
        watchdog.WATCHDOG_ROOT = self.old_watchdog_root
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_codex_classifier_uses_codex_specific_signals_only(self):
        self.assertFalse(watchdog._is_codex_payload({"model": "gpt-5.5"}))
        self.assertFalse(watchdog._is_codex_payload({"transcript_path": "C:/tmp/claude.jsonl", "model": "gpt-5.5"}))
        self.assertTrue(watchdog._is_codex_payload({"transcript_path": "C:/Users/me/.codex/sessions/abc.jsonl"}))

        with temp_dir() as tmp:
            transcript = Path(tmp) / "not-in-codex.jsonl"
            write_jsonl(transcript, [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"input_tokens": 10, "output_tokens": 5},
                            "model_context_window": 258400,
                        },
                    },
                }
            ])
            self.assertTrue(watchdog._is_codex_payload({"transcript_path": str(transcript)}))

    def test_usage_summary_reads_codex_token_count_window(self):
        with temp_dir() as tmp:
            transcript = Path(tmp) / ".codex" / "sessions" / "session.jsonl"
            write_jsonl(transcript, [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 1000,
                                "output_tokens": 250,
                                "total_tokens": 1250,
                            },
                            "total_token_usage": {"total_tokens": 99000},
                            "model_context_window": 258400,
                        },
                    },
                }
            ])

            used, peak, model, window = watchdog._usage_summary(str(transcript), "gpt-5.5")

            self.assertEqual(used, 1250)
            self.assertEqual(peak, 1250)
            self.assertEqual(model, "gpt-5.5")
            self.assertEqual(window, 258400)

    def test_codex_and_claude_ledgers_are_physically_separate(self):
        with temp_dir() as tmp:
            watchdog.STATE_DIR = tmp
            now = datetime.now().isoformat(timespec="seconds")
            for used in (220000, 230000, 240000):
                watchdog._log_compaction({
                    "ts": now,
                    "trigger": "auto",
                    "runtime": "codex",
                    "used_tokens": used,
                    "window": 258400,
                })
            watchdog._log_compaction({
                "ts": now,
                "trigger": "auto",
                "runtime": "claude",
                "used_tokens": 900000,
                "window": 1000000,
                "model": "opus",
            })

            codex_log = Path(watchdog._compaction_log_path("codex"))
            claude_log = Path(watchdog._compaction_log_path("claude"))

            self.assertEqual(codex_log.name, "compaction_log.codex.jsonl")
            self.assertEqual(claude_log.name, "compaction_log.jsonl")
            self.assertTrue(codex_log.exists())
            self.assertTrue(claude_log.exists())
            self.assertEqual(watchdog._recent_auto_points(258400, "codex"), [220000, 230000, 240000])
            self.assertEqual(watchdog._recent_auto_points(200000, "codex"), [])

    def test_codex_precompact_writes_snapshot_and_codex_ledger_only(self):
        with temp_dir() as tmp:
            project = Path(tmp) / "project"
            state = Path(tmp) / "state"
            watchdog.STATE_DIR = str(state)
            watchdog.WATCHDOG_ROOT = ""
            transcript = project / ".codex" / "sessions" / "session.jsonl"
            write_jsonl(transcript, [
                {
                    "timestamp": "2026-07-01T12:00:00",
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "Please keep my notes safe."},
                },
                {
                    "timestamp": "2026-07-01T12:00:01",
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "I will write the snapshot."},
                },
                {
                    "timestamp": "2026-07-01T12:00:02",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"input_tokens": 1000, "output_tokens": 250},
                            "model_context_window": 258400,
                        },
                    },
                },
            ])
            payload = {
                "hook_event_name": "PreCompact",
                "trigger": "auto",
                "session_id": "sess-1",
                "turn_id": "turn-2",
                "cwd": str(project),
                "model": "gpt-5.5",
                "transcript_path": str(transcript),
            }

            with contextlib.redirect_stdout(io.StringIO()):
                watchdog.handle_precompact(payload)

            latest = project / ".codex" / "continuity" / "latest.md"
            self.assertTrue(latest.exists())
            body = latest.read_text(encoding="utf-8")
            self.assertIn("# Codex Continuity Snapshot", body)
            self.assertIn("Please keep my notes safe.", body)
            self.assertTrue((state / "compaction_log.codex.jsonl").exists())
            self.assertFalse((state / "compaction_log.jsonl").exists())


    def test_codex_stop_banner_uses_codex_calibration_estimate(self):
        with temp_dir() as tmp:
            project = Path(tmp) / "project"
            state = Path(tmp) / "state"
            watchdog.STATE_DIR = str(state)
            watchdog.WATCHDOG_ROOT = ""
            transcript = project / ".codex" / "sessions" / "session.jsonl"
            write_jsonl(transcript, [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"input_tokens": 199000, "output_tokens": 1000},
                            "model_context_window": 258400,
                        },
                    },
                }
            ])
            now = datetime.now().isoformat(timespec="seconds")
            for used in (180000, 185000, 190000):
                watchdog._log_compaction({
                    "ts": now,
                    "trigger": "auto",
                    "runtime": "codex",
                    "used_tokens": used,
                    "window": 258400,
                })
            for used in (90000, 95000, 100000):
                watchdog._log_compaction({
                    "ts": now,
                    "trigger": "auto",
                    "runtime": "claude",
                    "used_tokens": used,
                    "window": 200000,
                    "model": "haiku",
                })
            payload = {
                "hook_event_name": "Stop",
                "session_id": "stop-1",
                "cwd": str(project),
                "model": "gpt-5.5",
                "transcript_path": str(transcript),
            }

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                watchdog.handle_stop(payload)

            out = json.loads(buf.getvalue())
            self.assertEqual(out["decision"], "block")
            self.assertIn("auto-compacts near 180,000", out["reason"])
            self.assertNotIn("auto-compacts near 90,000", out["reason"])

if __name__ == "__main__":
    unittest.main()
