#!/usr/bin/env python3
"""Deterministic transaction/receipt tests. No real harness or network calls."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("messaging", HERE / "agent-message.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
a = m.adapters


def user(text, harness="codex", **extra):
    if harness == "codex":
        return {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": text}]}, **extra}
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


class Receipts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "log.jsonl"
        self.log.write_text("")

    def append(self, record, complete=True):
        with self.log.open("a") as stream:
            stream.write(json.dumps(record) + ("\n" if complete else ""))

    def test_only_new_exact_user_messages(self):
        self.append(user("AM-unique pointer"))
        before = a.baseline(str(self.log))
        self.append({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                      "content": [{"type": "input_text", "text": "AM-unique pointer"}]}})
        self.append(user("quoted AM-unique pointer"))
        self.append(user("AM-unique pointer changed"))
        self.assertEqual(a.receipt(str(self.log), before, "codex", "AM-unique pointer"), "missing")
        self.append(user("AM-unique pointer"))
        self.assertEqual(a.receipt(str(self.log), before, "codex", "AM-unique pointer"), "received")

    def test_partial_receipt_then_delayed_completion(self):
        before = a.baseline(str(self.log))
        self.append(user("pointer"), complete=False)
        self.assertEqual(a.receipt(str(self.log), before, "codex", "pointer"), "missing")
        with self.log.open("a") as stream:
            stream.write("\n")
        self.assertEqual(a.receipt(str(self.log), before, "codex", "pointer"), "received")

    def test_partial_baseline_fails_closed(self):
        self.append(user("old"), complete=False)
        with self.assertRaises(a.EvidenceError):
            a.baseline(str(self.log))

    def test_rewrite_and_replace_fail_closed(self):
        self.append(user("old"))
        before = a.baseline(str(self.log))
        self.log.write_text("")
        with self.assertRaises(a.EvidenceError):
            a.receipt(str(self.log), before, "codex", "new")
        self.append(user("old"))
        before = a.baseline(str(self.log))
        other = self.log.with_suffix(".tmp")
        other.write_text(self.log.read_text() + json.dumps(user("new")) + "\n")
        other.replace(self.log)
        with self.assertRaises(a.EvidenceError):
            a.receipt(str(self.log), before, "codex", "new")

    def test_large_baseline_anchor(self):
        for _ in range(100):
            self.append(user("old " * 100))
        before = a.baseline(str(self.log))
        self.append(user("new"))
        self.assertEqual(a.receipt(str(self.log), before, "codex", "new"), "received")

    def test_claude_meta_sidechain_tool_results_rejected(self):
        before = a.baseline(str(self.log))
        self.append(user("pointer", "claude", isMeta=True))
        self.append(user("pointer", "claude", isSidechain=True))
        self.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "pointer"}]}})
        self.assertEqual(a.receipt(str(self.log), before, "claude", "pointer"), "missing")
        self.append(user("pointer", "claude"))
        self.assertEqual(a.receipt(str(self.log), before, "claude", "pointer"), "received")


class Attempts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "attempt.json"
        self.unique = "a" * 32
        self.payload = f"AM-{self.unique} pointer"
        import hashlib
        message = self.path.parent / "message"
        message.write_text(self.payload)
        message.chmod(0o600)
        self.record = {"schema": 1, "id": self.unique, "sha256": hashlib.sha256(self.payload.encode()).hexdigest(),
            "harness": "codex", "status": "prepared", "submissions": 0,
            "tmux": ["tmux", "-L", "disposable"], "target": {"pane": "%7"},
            "submit_delay": 0, "render_wait": 1, "max_wait": 1, "receipt_wait": 1}
        m.atomic_json(self.path, self.record)

    def deliver(self, continuation=False):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return m.deliver(self.path, self.record, self.payload, continuation)

    def test_unconsumed_first_enter_then_valid_continuation(self):
        result = type("Result", (), {"returncode": 0, "stderr": "", "stdout": "status=sent receipt=unverified\n"})()
        with patch.object(m, "observe", return_value="missing"), patch.object(m, "wait_receipt", side_effect=["missing", "received"]), patch.object(m.subprocess, "run", return_value=result) as transport:
            self.assertEqual(self.deliver(), 3)
            self.assertEqual(self.deliver(True), 0)
        first, second = [call.args[0] for call in transport.call_args_list]
        self.assertNotIn("--submit-pending", first)
        self.assertIn("--submit-pending", second)
        self.assertEqual(m.load_attempt(self.path)[0]["submissions"], 2)

    def test_delayed_receipt_no_duplicate(self):
        result = type("Result", (), {"returncode": 0, "stderr": "", "stdout": "status=sent receipt=unverified\n"})()
        with patch.object(m, "observe", side_effect=["missing", "received"]), patch.object(m, "wait_receipt", return_value="missing"), patch.object(m.subprocess, "run", return_value=result) as transport:
            self.assertEqual(self.deliver(), 3)
            self.assertEqual(self.deliver(True), 0)
            self.assertEqual(transport.call_count, 1)

    def test_pending_receipt_no_execution_wait(self):
        with patch.object(m, "observe", return_value="pending-steer"), patch.object(m.subprocess, "run") as transport:
            self.assertEqual(self.deliver(True), 0)
            transport.assert_not_called()

    def test_verified_guard_receipt_survives_immediate_rewrite(self):
        result = type("Result", (), {"returncode": 0, "stderr": "",
            "stdout": "status=received receipt=pending-steer\n"})()
        with patch.object(m, "observe", return_value="missing"), patch.object(m, "wait_receipt", side_effect=a.EvidenceError("rewritten")) as wait, patch.object(m.subprocess, "run", return_value=result):
            self.assertEqual(self.deliver(), 0)
            wait.assert_not_called()

    def test_changed_target_unknown_evidence_no_transport(self):
        with patch.object(m, "observe", side_effect=a.EvidenceError("receiver changed")), patch.object(m.subprocess, "run") as transport:
            with self.assertRaises(a.EvidenceError):
                self.deliver(True)
            transport.assert_not_called()

    def test_wrong_payload_and_permissions_rejected(self):
        (self.path.parent / "message").write_text(self.payload + " changed")
        with self.assertRaises(a.EvidenceError):
            m.load_attempt(self.path)
        self.path.chmod(0o644)
        with self.assertRaises(a.EvidenceError):
            m.load_attempt(self.path)

    def test_continuation_limit_no_transport(self):
        self.record.update(status="ambiguous", submissions=2)
        m.atomic_json(self.path, self.record)
        with patch.object(m, "observe", return_value="missing"), patch.object(m.subprocess, "run") as transport:
            self.assertEqual(self.deliver(True), 3)
            transport.assert_not_called()

    def test_oversized_preflight_no_receiver_lookup_or_keys(self):
        with patch.object(m, "identity", return_value={"width": 40}), patch.object(a, "receiver") as receiver, patch.object(m.subprocess, "run") as transport, patch.object(m.sys, "argv", ["agent-message", "send", "%7", "--harness", "codex", "--message", "a long pointer that cannot fit in forty columns"]), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(m.main(), 2)
            receiver.assert_not_called()
            transport.assert_not_called()

    def test_guard_wrong_payload_or_target_no_observation(self):
        self.record["target"]["server"] = 4242
        m.atomic_json(self.path, self.record)
        wrong = self.path.parent / "wrong"
        wrong.write_text(self.payload + " different")
        for target, file in [("4242:%8", self.path.parent / "message"), ("4242:%7", wrong)]:
            with patch.object(m, "observe") as observe, patch.object(m.sys, "argv", ["agent-message", "guard", str(self.path), target, str(file)]), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(m.main(), 3)
                observe.assert_not_called()


class PendingReceipt(unittest.TestCase):
    def frame(self, payload, queue=True):
        return ("• Working (3s • esc to interrupt)\n\n"
                "• Messages to be submitted after next tool call\n"
                f"\x1b[2m  ↳ {payload}\x1b[0m\n\n"
                "› \x1b[2mAsk Codex to do anything\x1b[0m\n\n"
                "  GPT-6-Sol · Context 93% left · disposable\n"
                "  \x1b[1mtab\x1b[0m to queue message")

    def check(self, frame, payload, cursor="2:5"):
        with patch.object(m, "run", side_effect=[frame, cursor]):
            return m.pending_receipt(["tmux"], {"harness": "codex", "target": {"pane": "%7"}}, payload)

    def test_exact_dim_pending_widget(self):
        self.assertEqual(self.check(self.frame("AM-new pointer"), "AM-new pointer"), "pending-steer")

    def test_unstyled_quoted_wrong_or_collapsed_not_receipts(self):
        frame = self.frame("AM-new pointer")
        for edited in [frame.replace("\x1b[2m  ↳ AM-new pointer\x1b[0m", "  ↳ AM-new pointer"),
                       frame.replace("AM-new pointer", "quoted AM-new pointer"),
                       frame.replace("AM-new pointer", "AM-new pointer …"),
                       frame.replace("after next tool call", "at end of turn"),
                       frame.replace("\n\n›", "\nassistant output\n›")]:
            self.assertEqual(self.check(edited, "AM-new pointer"), "missing")

    def test_remaining_own_composer_never_counts_as_queue(self):
        frame = self.frame("AM-new pointer").replace("Ask Codex to do anything", "AM-new pointer")
        self.assertEqual(self.check(frame, "AM-new pointer", "16:5"), "missing")


if __name__ == "__main__":
    unittest.main()
