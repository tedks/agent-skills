#!/usr/bin/env python3
"""Read-only receiver identity and receipts; no keys, hooks or native delivery.

Adapters require a live process association, not a guessed latest transcript.
Unknown evidence raises EvidenceError and never authorizes submission.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


class EvidenceError(Exception):
    pass


UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def claude_registry_receiver(pid: int, args: list[bytes]) -> dict:
    """Read current application registry, including attached background engines.

    A startup --resume argument or a latest-on-disk transcript is not current
    session evidence: a person can switch sessions while the process survives.
    """
    rows = json.loads(subprocess.check_output(["claude", "agents", "--json"],
                    text=True, stderr=subprocess.DEVNULL, timeout=10))
    if not isinstance(rows, list):
        raise EvidenceError("unknown Claude registry schema")
    attach = None
    if len(args) >= 3 and args[1] == b"attach":
        attach = args[2].decode("ascii")
    matches = [row for row in rows if isinstance(row, dict) and
               (row.get("kind") == "interactive" and row.get("pid") == pid
                or attach and row.get("kind") == "background" and row.get("id") == attach)]
    if len(matches) != 1:
        raise EvidenceError("no unique live Claude registry identity")
    row = matches[0]
    session = row.get("sessionId")
    engine = row.get("pid")
    if not isinstance(session, str) or not UUID.fullmatch(session) or type(engine) is not int or engine < 2:
        raise EvidenceError("invalid Claude registry identity")
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    logs = list((root / "projects").glob(f"*/{session}.jsonl"))
    if len(logs) != 1:
        raise EvidenceError("no unique Claude registry transcript")
    return {"log": str(logs[0]), "engine_pid": engine, "engine_start": process_stamp(engine),
            "session": session}


def process_stamp(pid: int) -> str:
    # comm may contain spaces and parentheses; fields start after its LAST ')'.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return fields[19]  # kernel field 22: process start ticks


def descendants(pid: int) -> list[int]:
    result = [pid]
    for current in result:
        try:
            children = Path(f"/proc/{current}/task/{current}/children").read_text()
        except FileNotFoundError:
            continue
        result.extend(int(child) for child in children.split() if int(child) not in result)
    return result


def open_logs(pid: int) -> set[str]:
    paths = set()
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            path = os.readlink(fd)
        except FileNotFoundError:
            continue
        if path.endswith(".jsonl") and not path.endswith(" (deleted)"):
            paths.add(path)
    return paths


def receiver(pane_pid: int, harness: str) -> dict:
    """Bind the single selected harness process to its application transcript."""
    found = []
    for pid in descendants(pane_pid):
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            executable = Path(os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")).name
            stamp = process_stamp(pid)
            logs = open_logs(pid)
        except FileNotFoundError:
            continue
        if harness == "codex":
            # Exclude npm's node launcher and its app-server/MCP descendants.
            if executable != "codex":
                continue
            if any(arg in {b"app-server", b"exec", b"queue", b"mcp-server"} for arg in args[1:]):
                continue
            logs = {p for p in logs if "/sessions/" in p and Path(p).name.startswith("rollout-")}
        elif harness == "claude":
            if executable not in {"claude", "claude.exe"}:
                continue
            registered = claude_registry_receiver(pid, args)
            found.append({"pid": pid, "start": stamp, **registered})
            continue
        else:
            raise EvidenceError("unsupported harness")
        if len(logs) == 1:
            found.append({"pid": pid, "start": stamp, "log": next(iter(logs))})
        elif len(logs) > 1:
            raise EvidenceError("multiple receiver transcripts")
    if len(found) != 1:
        raise EvidenceError("no unique live receiver transcript")
    return found[0]


def baseline(path: str) -> dict:
    with open(path, "rb") as stream:
        st = os.fstat(stream.fileno())
        stream.seek(max(0, st.st_size - 4096))
        data = stream.read(4096)
    if data and not data.endswith(b"\n"):
        # A partly written old record must not become a new receipt.
        raise EvidenceError("receiver transcript has an incomplete baseline record")
    return {"dev": st.st_dev, "ino": st.st_ino, "offset": st.st_size,
            "anchor": hashlib.sha256(data[-4096:]).hexdigest()}


def user_text(record: dict, harness: str) -> str | None:
    if harness == "codex":
        if record.get("type") != "response_item":
            return None
        message = record.get("payload", {})
        if not isinstance(message, dict):
            return None
        if message.get("type") != "message" or message.get("role") != "user":
            return None
        content = message.get("content")
        kind = "input_text"
    elif harness == "claude":
        if record.get("type") != "user" or record.get("isMeta") or record.get("isSidechain"):
            return None
        message = record.get("message", {})
        if not isinstance(message, dict):
            return None
        if message.get("role") != "user":
            return None
        content = message.get("content")
        kind = "text"
    else:
        raise EvidenceError("unsupported harness")
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        return None
    if any(not isinstance(item, dict) or item.get("type") != kind for item in content):
        return None
    texts = [item.get("text") for item in content]
    return "".join(texts) if all(isinstance(text, str) for text in texts) else None


def receipt(path: str, before: dict, harness: str, payload: str) -> str:
    """Only exact new complete application user records count as receipts.

    Rewrite/shrink or malformed data remain unknown. A new message UUID and
    exact payload are required by the caller, so inherited history cannot match.
    """
    with open(path, "rb") as stream:
        st = os.fstat(stream.fileno())
        if (st.st_dev, st.st_ino) != (before["dev"], before["ino"]) or st.st_size < before["offset"]:
            raise EvidenceError("receiver transcript replaced or shrunk")
        stream.seek(max(0, before["offset"] - 4096))
        anchor = stream.read(min(4096, before["offset"]))
        if hashlib.sha256(anchor).hexdigest() != before["anchor"]:
            raise EvidenceError("receiver transcript baseline changed")
        for line in stream:
            if not line.endswith(b"\n"):
                break
            record = json.loads(line)
            if not isinstance(record, dict):
                raise EvidenceError("invalid receiver transcript record")
            if user_text(record, harness) == payload:
                return "received"
    return "missing"
