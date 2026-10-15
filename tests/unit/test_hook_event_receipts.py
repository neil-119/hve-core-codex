from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter import hook_runtime  # noqa: E402
from hve_codex_porter.hook_runtime import (  # noqa: E402
    _MAX_HOOK_RECEIPT_BYTES,
    handle_event,
)


class HookEventReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.rules_dir = self.root / "rules"
        self.rules_dir.mkdir()
        self.state_dir = self.root / "hook-state"
        self.previous_state_dir = os.environ.get("HVE_CODEX_HOOK_STATE_DIR")
        self.previous_audit = os.environ.get("HVE_CODEX_HOOK_AUDIT")
        os.environ["HVE_CODEX_HOOK_STATE_DIR"] = str(self.state_dir)
        os.environ.pop("HVE_CODEX_HOOK_AUDIT", None)
        self.write_registry()

    def tearDown(self) -> None:
        if self.previous_state_dir is None:
            os.environ.pop("HVE_CODEX_HOOK_STATE_DIR", None)
        else:
            os.environ["HVE_CODEX_HOOK_STATE_DIR"] = self.previous_state_dir
        if self.previous_audit is None:
            os.environ.pop("HVE_CODEX_HOOK_AUDIT", None)
        else:
            os.environ["HVE_CODEX_HOOK_AUDIT"] = self.previous_audit
        self.temporary.cleanup()

    @property
    def receipt_path(self) -> Path:
        return self.state_dir / "hook-event-receipts.json"

    def write_registry(self) -> None:
        (self.rules_dir / "style.md").write_text("Follow the synthetic scoped rule.", encoding="utf-8")
        (self.rules_dir / "index.json").write_text(
            json.dumps({
                "version": 1,
                "rules": [{
                    "id": "style",
                    "source": ".github/instructions/style.instructions.md",
                    "path": "style.md",
                    "apply_to": ["src/**/*.py"],
                    "precedence": 10,
                    "imports": [],
                }],
            }),
            encoding="utf-8",
        )

    def read_receipts(self) -> list[dict[str, object]]:
        value = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(value["version"], 1)
        return value["receipts"]

    def user_prompt(self, *, session: str, turn: str, prompt: str) -> dict[str, object]:
        return {
            "hook_event_name": "UserPromptSubmit",
            "session_id": session,
            "turn_id": turn,
            "cwd": str(self.root),
            "prompt": prompt,
        }

    def test_default_off_emits_context_without_creating_receipt_file(self) -> None:
        result = handle_event(self.user_prompt(session="session-one", turn="turn-one", prompt="Edit src/app.py"))
        self.assertIsNotNone(result)
        self.assertIn("Follow the synthetic scoped rule.", result["hookSpecificOutput"]["additionalContext"])
        self.assertFalse(self.receipt_path.exists())

    def test_receipts_capture_actual_event_names_and_outcomes_including_session_start(self) -> None:
        os.environ["HVE_CODEX_HOOK_AUDIT"] = "1"
        positive = self.user_prompt(session="session-one", turn="turn-positive", prompt="Edit src/app.py")
        negative = self.user_prompt(session="session-one", turn="turn-negative", prompt="Explain the design without naming a file")
        repeated = self.user_prompt(session="session-one", turn="turn-dedup", prompt="Edit src/app.py")

        self.assertIsNotNone(handle_event(positive, script_path=hook_runtime.__file__))
        self.assertIsNone(handle_event(negative, script_path=hook_runtime.__file__))
        self.assertIsNotNone(handle_event(repeated, script_path=hook_runtime.__file__))
        self.assertIsNone(handle_event(repeated, script_path=hook_runtime.__file__))

        session_start = {
            "hook_event_name": "SessionStart",
            "session_id": "session-start",
            "source": "new",
            "cwd": str(self.root),
        }
        self.assertIsNotNone(handle_event(session_start, script_path=hook_runtime.__file__))
        self.assertIsNotNone(handle_event(session_start, script_path=hook_runtime.__file__))

        receipts = self.read_receipts()
        self.assertEqual([receipt["event_name"] for receipt in receipts], [
            "UserPromptSubmit", "UserPromptSubmit", "UserPromptSubmit", "UserPromptSubmit", "SessionStart", "SessionStart",
        ])
        self.assertEqual([receipt["outcome"] for receipt in receipts], [
            "context_emitted", "no_match", "context_emitted", "dedup", "context_emitted", "context_emitted",
        ])
        self.assertIsNone(receipts[-1]["invocation_id_sha256"], "SessionStart bypasses dedup but still records a receipt")
        self.assertEqual(receipts[-1]["session_id_sha256"], hashlib.sha256(b"session-start").hexdigest())

    def test_receipt_contains_only_validated_hash_fields_not_prompt_ids_paths_or_rule_content(self) -> None:
        os.environ["HVE_CODEX_HOOK_AUDIT"] = "1"
        session = "SECRET_SESSION_IDENTIFIER"
        turn = "SECRET_TURN_IDENTIFIER"
        prompt = "Please edit src/private_customer_file.py with SECRET_PROMPT_TEXT"
        result = handle_event(self.user_prompt(session=session, turn=turn, prompt=prompt), script_path=hook_runtime.__file__)
        self.assertIsNotNone(result)
        context = result["hookSpecificOutput"]["additionalContext"]
        receipt = self.read_receipts()[0]
        receipt_bytes = self.receipt_path.read_bytes()

        self.assertEqual(receipt["session_id_sha256"], hashlib.sha256(session.encode()).hexdigest())
        self.assertEqual(receipt["invocation_id_sha256"], hashlib.sha256(turn.encode()).hexdigest())
        self.assertEqual(receipt["context_sha256"], hashlib.sha256(context.encode()).hexdigest())
        self.assertEqual(receipt["rule_content_sha256"], [hashlib.sha256(b"Follow the synthetic scoped rule.").hexdigest()])
        script_bytes = Path(hook_runtime.__file__).read_bytes()
        self.assertEqual(receipt["script_sha256"], hashlib.sha256(script_bytes).hexdigest())
        allowed_fields = {
            "event_name", "timestamp_utc", "session_id_sha256", "invocation_id_sha256", "outcome",
            "rule_content_sha256", "context_sha256", "script_sha256",
        }
        self.assertEqual(set(receipt), allowed_fields)
        for private_value in (session, turn, prompt, "src/private_customer_file.py", "Follow the synthetic scoped rule.", str(self.root)):
            self.assertNotIn(private_value.encode(), receipt_bytes)

    def test_malformed_oversized_and_long_history_is_recovered_with_both_bounds(self) -> None:
        os.environ["HVE_CODEX_HOOK_AUDIT"] = "1"
        self.state_dir.mkdir(parents=True)
        self.receipt_path.write_text("{malformed", encoding="utf-8")
        start = {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(self.root)}
        self.assertIsNotNone(handle_event(start, script_path=hook_runtime.__file__))
        self.assertEqual(len(self.read_receipts()), 1)

        poisoned = {
            "version": 1,
            "receipts": [{
                "event_name": [],
                "timestamp_utc": "2026-10-11T12:00:00.000+00:00",
                "outcome": "context_emitted",
                "rule_content_sha256": [],
                "prompt": "PRIVATE_CORRUPT_HISTORY",
            }],
        }
        self.receipt_path.write_text(json.dumps(poisoned), encoding="utf-8")
        self.assertIsNotNone(handle_event(start, script_path=hook_runtime.__file__))
        self.assertEqual(len(self.read_receipts()), 1)
        self.assertNotIn(b"PRIVATE_CORRUPT_HISTORY", self.receipt_path.read_bytes())

        self.receipt_path.write_bytes(b"x" * (_MAX_HOOK_RECEIPT_BYTES + 1))
        self.assertIsNotNone(handle_event(start, script_path=hook_runtime.__file__))
        self.assertEqual(len(self.read_receipts()), 1, "oversized history is discarded before the new receipt is written")

        baseline = {
            "event_name": "SessionStart",
            "timestamp_utc": "2026-10-11T12:00:00.000+00:00",
            "session_id_sha256": "a" * 64,
            "invocation_id_sha256": None,
            "outcome": "context_emitted",
            "rule_content_sha256": [],
        }
        self.receipt_path.write_text(json.dumps({"version": 1, "receipts": [baseline] * 300}), encoding="utf-8")
        self.assertIsNotNone(handle_event(start, script_path=hook_runtime.__file__))
        receipts = self.read_receipts()
        self.assertLessEqual(len(receipts), 256)
        self.assertLessEqual(self.receipt_path.stat().st_size, _MAX_HOOK_RECEIPT_BYTES)

    def test_blocked_receipt_write_does_not_change_emitted_context(self) -> None:
        os.environ["HVE_CODEX_HOOK_AUDIT"] = "1"
        start = {"hook_event_name": "SessionStart", "session_id": "session-start", "cwd": str(self.root)}
        with patch.object(hook_runtime.os, "replace", side_effect=PermissionError("receipt directory is read-only")):
            result = handle_event(start, script_path=hook_runtime.__file__)
        self.assertIsNotNone(result)
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("HVE Core scoped-rule guidance", result["hookSpecificOutput"]["additionalContext"])
        self.assertFalse(self.receipt_path.exists())
        self.assertEqual(list(self.state_dir.glob("hve-hook-audit-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
