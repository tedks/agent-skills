#!/usr/bin/env python3
"""Runtime integration tests that require the package's minimal executable PATH."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import harness_control
import server


class RuntimeTest(unittest.TestCase):
    def test_observer_reenters_the_isolated_module(self):
        command = harness_control._observer_command(Path("/state"), "a" * 32)
        self.assertEqual(
            [os.path.realpath(sys.executable), "-I", "-m", "harness_control",
             "--observe", "/state", "a" * 32],
            [os.path.realpath(command[0]), *command[1:]],
        )

    def test_checkpoint_verification_finds_packaged_git(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Harness Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "harness@example.invalid"], check=True)
            shard = repo / "handoff.md"
            shard.write_text("recoverable state\n")
            subprocess.run(["git", "-C", str(repo), "add", "handoff.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "checkpoint"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            result = harness_control.verify_checkpoint({
                "private_storage_attested": True,
                "obligations_preserved": True,
                "pause_state_preserved": True,
                "resources_disposition": "active_recorded",
                "authority": "compact_authorized",
                "pause_state_reference": "none",
                "active_inventory_reference": "test",
                "push_attestation": "no_remote_configured",
                "sha256": hashlib.sha256(shard.read_bytes()).hexdigest(),
                "commit": commit,
                "repo_path": str(repo),
                "shard_path": str(shard),
            })
            self.assertIn("shard_bytes_in_exact_commit", result["verified"])

    def test_session_state_rejects_traversal_and_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir()
            old = server.SESSION_STATE_DIR
            server.SESSION_STATE_DIR = state_dir
            self.addCleanup(setattr, server, "SESSION_STATE_DIR", old)
            outside = root / "outside.json"
            outside.write_text('{"launch_dir": "/private"}')
            self.assertEqual({}, server._read_session_state("../outside"))
            sid = "00000000-0000-0000-0000-000000000000"
            (state_dir / f"{sid}.json").write_text("not json")
            self.assertEqual({}, server._read_session_state(sid))


if __name__ == "__main__":
    unittest.main()
