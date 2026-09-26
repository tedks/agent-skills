#!/usr/bin/env python3
"""Durable, receipt-aware attempts around the canonical guarded tmux sender.

No native message delivery, no blind repaste, and at most one continuation.
Harness-specific identity/receipt evidence is isolated in the adapter module.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def module(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


adapters = module("agent-message-adapters")
screen_parser = module("claude-send-screen")


def tmux_command() -> list[str]:
    if os.environ.get("SPAWN_TMUX_SOCKET"):
        return ["tmux", "-S", os.environ["SPAWN_TMUX_SOCKET"]]
    if os.environ.get("SPAWN_TMUX_LABEL"):
        return ["tmux", "-L", os.environ["SPAWN_TMUX_LABEL"]]
    if os.environ.get("TMUX"):
        return ["tmux", "-S", os.environ["TMUX"].split(",", 1)[0]]
    return ["tmux"]


def run(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=10).rstrip("\n")


def identity(command: list[str], target: str) -> dict:
    data = run(command + ["display-message", "-p", "-t", target,
                          "#{pid}:#{pane_id}:#{pane_pid}:#{pane_width}"])
    match = re.fullmatch(r"(\d+):(%\d+):(\d+):(\d+)", data)
    if not match:
        raise adapters.EvidenceError("invalid tmux receiver identity")
    server, pane, pid, width = match.groups()
    return {"server": int(server), "server_start": adapters.process_stamp(int(server)),
            "pane": pane, "pane_pid": int(pid), "pane_start": adapters.process_stamp(int(pid)),
            "width": int(width)}


def private(path: Path, directory: bool = False) -> None:
    st = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise adapters.EvidenceError("unsafe attempt storage")


def atomic_json(path: Path, data: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_attempt(path: Path) -> tuple[dict, str]:
    private(path.parent, directory=True)
    private(path)
    private(path.parent / "message")
    record = json.loads(path.read_text())
    payload = (path.parent / "message").read_bytes().decode("utf-8")
    if (not isinstance(record, dict) or record.get("schema") != 1
            or not isinstance(record.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", record["id"])
            or not payload.startswith(f"AM-{record['id']} ")
            or any(ord(ch) < 32 or ord(ch) > 126 for ch in payload)
            or payload != payload.strip()
            or hashlib.sha256(payload.encode()).hexdigest() != record.get("sha256")):
        raise adapters.EvidenceError("attempt payload or identity changed")
    command = record.get("tmux")
    if (not isinstance(command, list) or not command or command[0] != "tmux"
            or not (len(command) == 1 or len(command) == 3 and command[1] in {"-S", "-L"}
                    and isinstance(command[2], str) and command[2] and not command[2].startswith("-"))
            or record.get("harness") not in {"codex", "claude"}
            or type(record.get("submissions")) is not int or not 0 <= record["submissions"] <= 2
            or record.get("status") not in {"prepared", "deferred", "ambiguous", "received"}):
        raise adapters.EvidenceError("invalid attempt transport policy")
    for key, minimum, maximum in [("submit_delay", 0, 30), ("render_wait", 1, 30),
                                  ("max_wait", 1, 600), ("receipt_wait", 1, 60)]:
        if type(record.get(key)) is not int or not minimum <= record[key] <= maximum:
            raise adapters.EvidenceError("invalid attempt timing policy")
    return record, payload


def pending_receipt(command: list[str], record: dict, payload: str) -> str:
    """Conservative measured Codex pending-steer widget; never history search."""
    if record["harness"] != "codex":
        return "missing"
    pane = record["target"]["pane"]
    raw = run(command + ["capture-pane", "-e", "-p", "-t", pane])
    cursor = run(command + ["display-message", "-p", "-t", pane, "#{cursor_x}:#{cursor_y}"])
    cx, cy = map(int, cursor.split(":"))
    if screen_parser.state(raw, payload, cx, cy) != "empty":
        return "missing"
    rows = raw.splitlines()
    clean = [screen_parser.strip_ansi(row).rstrip() for row in rows]
    prompts = [i for i, row in enumerate(clean) if row.startswith("› ")]
    if not prompts:
        return "missing"
    prompt = prompts[-1]
    header = "• Messages to be submitted after next tool call"
    headers = [i for i, row in enumerate(clean[:prompt]) if row == header
               or row.startswith(header + " (press ") and row.endswith(" to interrupt and send immediately)")]
    if not headers:
        return "missing"
    start = headers[-1]
    # Require the final widget directly above the composer, with dim exact
    # unwrapped payload and no transcript/other surface mixed into its rows.
    own = False
    for index in range(start + 1, prompt):
        row = clean[index]
        if not row:
            continue
        if row.startswith("  (press ") and row.endswith(" to interrupt and send immediately)"):
            continue
        if not row.startswith("  ↳ ") or not screen_parser.dim_span(rows[index], row):
            return "missing"
        if row == "  ↳ " + payload:
            own = True
    return "pending-steer" if own else "missing"


def observe(record: dict, payload: str) -> str:
    command = record["tmux"]
    current = identity(command, record["target"]["pane"])
    expected = record["target"]
    if any(current[key] != expected[key] for key in expected if key != "width"):
        raise adapters.EvidenceError("receiver target changed")
    if len(payload) > current["width"] - 8:
        raise adapters.EvidenceError("pointer no longer fits receiver pane")
    live = adapters.receiver(current["pane_pid"], record["harness"])
    if live != record["receiver"]:
        raise adapters.EvidenceError("receiver process or transcript changed")
    result = adapters.receipt(live["log"], record["baseline"], record["harness"], payload)
    return pending_receipt(command, record, payload) if result == "missing" else result


def output(record: dict, path: Path) -> None:
    print(json.dumps({"status": record["status"], "id": record["id"], "attempt_file": str(path),
                      "receipt": record.get("receipt", "unverified"),
                      "reason": record.get("reason", ""), "submissions": record["submissions"]}))


def wait_receipt(record: dict, payload: str, seconds: float) -> str:
    end = time.monotonic() + seconds
    while True:
        result = observe(record, payload)
        if result != "missing" or time.monotonic() >= end:
            return result
        time.sleep(min(0.2, max(0, end - time.monotonic())))


@contextmanager
def attempt_lock(path: Path):
    """Bound contention on one attempt; the sender separately locks its pane."""
    lock_path = path.parent / "attempt.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        private(lock_path)
        end = time.monotonic() + 90
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise adapters.EvidenceError("attempt lock timeout")
                time.sleep(0.2)
        yield


def deliver(path: Path, record: dict, payload: str, continuation: bool, *, publish: bool = True) -> int:
    with attempt_lock(path):
        record, payload = load_attempt(path)
        if record["status"] == "received":
            if publish:
                output(record, path)
            return 0
        result = observe(record, payload)
        if result != "missing":
            record.update(status="received", receipt=result, reason="")
            atomic_json(path, record)
            if publish:
                output(record, path)
            return 0
        if continuation and record["submissions"] >= 2:
            record.update(status="ambiguous", reason="continuation-limit")
            atomic_json(path, record)
            if publish:
                output(record, path)
            return 3
        if not continuation and record["submissions"]:
            raise adapters.EvidenceError("attempt already started; use continue")
        options = ["--submit-delay", str(record["submit_delay"]), "--render-wait", str(record["render_wait"]),
                   "--max-wait", str(record["max_wait"]), "--attempt-file", str(path)]
        if continuation:
            options.append("--submit-pending")
        # Record the intent BEFORE transport; crashes cannot authorize repaste.
        record["submissions"] += 1
        record.update(status="ambiguous", reason="transport-in-progress")
        atomic_json(path, record)
        env = {**os.environ}
        env.pop("SPAWN_TMUX_SOCKET", None)
        env.pop("SPAWN_TMUX_LABEL", None)
        if len(record["tmux"]) == 3:
            env["SPAWN_TMUX_SOCKET" if record["tmux"][1] == "-S" else "SPAWN_TMUX_LABEL"] = record["tmux"][2]
        else:
            env.pop("TMUX", None)
        completed = subprocess.run([str(HERE / "claude-send.sh"), record["target"]["pane"], *options,
                                    "--prompt-file", str(path.parent / "message")],
                                   env=env, capture_output=True, text=True)
        # Retained payload paths and statuses are safe; never emit composer text.
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        # Preserve a verified observation made by the canonical guard under
        # its pane lock. A receipt may disappear/rewrite immediately afterward;
        # transport exit zero alone is still never receipt evidence.
        guarded = {"status=received receipt=received\n": "received",
                   "status=received receipt=pending-steer\n": "pending-steer"}
        result = guarded.get(completed.stdout) if completed.returncode == 0 else None
        if result is None and completed.returncode == 0:
            result = wait_receipt(record, payload, record["receipt_wait"])
        if completed.returncode != 0:
            record.update(status="deferred" if completed.returncode == 2 else "ambiguous",
                          reason=f"transport-exit-{completed.returncode}")
            code = 2 if completed.returncode == 2 else 3
        elif result != "missing":
            record.update(status="received", receipt=result, reason="")
            code = 0
        else:
            record.update(status="deferred" if completed.returncode == 2 else "ambiguous",
                          reason="no-application-receipt")
            code = 2 if completed.returncode == 2 else 3
        atomic_json(path, record)
        if publish:
            output(record, path)
        return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send")
    send.add_argument("target")
    send.add_argument("--harness", required=True, choices=["codex", "claude"])
    body = send.add_mutually_exclusive_group(required=True)
    body.add_argument("--message")
    body.add_argument("--report-file", type=Path)
    send.add_argument("--submit-delay", type=int, choices=range(31), default=8)
    send.add_argument("--render-wait", type=int, choices=range(1, 31), default=1)
    send.add_argument("--max-wait", type=int, choices=range(1, 601), default=30)
    send.add_argument("--receipt-wait", type=int, choices=range(1, 61), default=5)
    send.add_argument("--no-continuation", action="store_true",
                      help="stop after the initial attempt instead of one guarded Enter-only continuation")
    for name in ["continue", "status", "guard"]:
        sub = commands.add_parser(name)
        sub.add_argument("attempt_file", type=Path)
        if name == "guard":
            sub.add_argument("transport_identity")
            sub.add_argument("payload_file", type=Path)
    args = parser.parse_args()
    path = None
    try:
        if args.command == "send":
            if args.report_file:
                report = args.report_file.resolve(strict=True)
                if not report.is_file():
                    raise adapters.EvidenceError("report is not a file")
                body = str(report)
            else:
                body = args.message
            if not body or body != body.strip() or any(ord(ch) < 32 or ord(ch) > 126 for ch in body):
                raise adapters.EvidenceError("send a short printable ASCII line without trailing newline/space")
            command = tmux_command()
            target = identity(command, args.target)
            unique = uuid.uuid4().hex
            payload = f"AM-{unique} {body}"
            if len(payload) > target["width"] - 8:
                raise adapters.EvidenceError("pointer does not fit receiving pane; shorten it before delivery")
            live = adapters.receiver(target["pane_pid"], args.harness)
            before = adapters.baseline(live["log"])
            root = Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")) / "agent-message"
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            private(root, directory=True)
            attempt = Path(tempfile.mkdtemp(prefix=f"{unique}-", dir=root))
            message = attempt / "message"
            message.write_text(payload)
            message.chmod(0o600)
            path = attempt / "attempt.json"
            record = {"schema": 1, "id": unique, "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                      "tmux": command, "target": target, "receiver": live, "baseline": before,
                      "harness": args.harness, "status": "prepared", "submissions": 0,
                      "submit_delay": args.submit_delay, "render_wait": args.render_wait,
                      "max_wait": args.max_wait, "receipt_wait": args.receipt_wait}
            atomic_json(path, record)
            code = deliver(path, record, payload, False, publish=False)
            record, payload = load_attempt(path)
            if (not args.no_continuation and code == 3 and record.get("reason") == "no-application-receipt"):
                return deliver(path, record, payload, True)
            output(record, path)
            return code
        path = args.attempt_file.resolve()
        record, payload = load_attempt(path)
        if args.command == "guard":
            expected = f"{record['target']['server']}:{record['target']['pane']}"
            if args.transport_identity != expected or args.payload_file.read_bytes().decode("utf-8") != payload:
                raise adapters.EvidenceError("transport target or payload does not match attempt")
            print(observe(record, payload))
            return 0
        if args.command == "continue":
            return deliver(path, record, payload, True)
        with attempt_lock(path):
            record, payload = load_attempt(path)
            if record["status"] != "received":
                result = observe(record, payload)
                if result != "missing":
                    record.update(status="received", receipt=result, reason="")
                    atomic_json(path, record)
        output(record, path)
        return 0 if record["status"] == "received" else 3
    except (adapters.EvidenceError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Invalid state cannot submit or silently drop the original payload.
        print(json.dumps({"status": "ambiguous" if path else "deferred", "reason": str(error),
                          "attempt_file": str(path) if path else None}), file=sys.stderr)
        return 3 if path else 2


if __name__ == "__main__":
    raise SystemExit(main())
