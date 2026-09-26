#!/usr/bin/env python3
"""Blocking tmux delivery. Screen evidence is heuristic, not atomic input ownership.

Only the live input is compared for typing; output may move freely. One lock
serializes cooperating senders through receipt, and one paste belongs to one
invocation. Uncertainty is returned to the caller rather than delegated silently.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("screen", Path(__file__).with_name("claude-send-screen.py"))
styles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(styles)

PROMPT = re.compile(r"^\s*([›❯])(?:[ \u00a0]|$)(.*)$")
PASTE = re.compile(r"\[Pasted (?:text|Text)(?:[^\]\n]*)\]", re.I)
FOOTER = re.compile(r"^\s*(?:.*(?:Context \d+%|\d+% context left| · )|tab to queue message|\? for shortcuts|esc to interrupt)", re.I)
MODAL = re.compile(r"Quick safety check|Yes, I trust this folder|No, exit|Do you trust the contents|Press enter to continue|Would you like to (?:proceed|run)|Do you want to (?:proceed|allow)|Allow once|Esc to cancel", re.I)
QUEUE = re.compile(r"Messages to be submitted|queued messages?", re.I)


CLAUDE_RESUME = re.compile(r"Resume this session with:\s*\n\s*claude --resume ([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?=\s|$)", re.I)


def resume_records(frame):
    return Counter(session.lower() for session in CLAUDE_RESUME.findall(styles.strip_ansi(frame.history or frame.raw)))


def normalized(text):
    # Line wrapping, indentation and terminal padding do not identify a message.
    return "".join(text.split())


@dataclass
class Frame:
    raw: str
    x: int
    y: int
    history: str = ""


@dataclass
class Input:
    kind: str
    text: str = ""
    start: int = 0
    end: int = 0
    cursor: tuple = (0, 0)
    harness: str = ""

    def fingerprint(self):
        # Relative cursor: output scrolling above the input is not human typing.
        return self.kind, self.text, self.cursor, self.harness


def composer(frame):
    lines = styles.strip_ansi(frame.raw).splitlines()
    raw = frame.raw.splitlines()
    if MODAL.search("\n".join(lines[-25:])):
        return Input("modal")
    # Locate a prompt whose input contains the cursor. Do not assume a fixed
    # row, model name, footer spacing, or number of wrapped rows.
    candidates = [(i, PROMPT.match(line)) for i, line in enumerate(lines) if i <= frame.y and PROMPT.match(line)]
    for idx, match in reversed(candidates):
        glyph, first = match.groups()
        if glyph == "❯":
            rules = [i for i, line in enumerate(lines) if line.strip() and set(line.strip()) == {"─"}]
            below = [i for i in rules if i > idx]
            if not any(i < idx for i in rules) or not below:
                continue
            end = below[0]
            harness = "claude"
        else:
            footers = [i for i in range(idx + 1, len(lines)) if FOOTER.match(lines[i])]
            if not footers:
                continue
            end = footers[0]
            harness = "codex"
        if not idx <= frame.y < end:
            continue
        parts = [first] + [line[2:] if line.startswith("  ") else line for line in lines[idx + 1:end]]
        text = "\n".join(parts).rstrip()
        cursor = (frame.x, frame.y - idx)
        kind = "empty" if not text.strip() else "text"
        if text and frame.y == idx and frame.x <= len(match.group(0)) - len(first):
            if harness == "codex" and styles.dim_span(raw[idx], first):
                kind = "empty"
                text = ""
            elif harness == "claude" and first.startswith('Try '):
                kind = "suggestion"
        return Input(kind, text, idx, end, cursor, harness)
    return Input("unknown")


def records(frame, inp):
    """Candidate receipts outside the live composer, with semantic row markers.

    A new occurrence is required relative to the pre-paste history. Assistant
    prose containing the text without a user/queue marker is not a receipt.
    """
    visible = frame.raw.splitlines()
    # History includes the visible screen. Cut off the live input using the
    # captured visible suffix instead of treating its prompt as submitted text.
    history = frame.history.splitlines() if frame.history else visible
    prefix_len = max(0, len(history) - len(visible))
    rows = history[:prefix_len + inp.start] if inp.kind not in {"unknown", "modal"} else history[:prefix_len]
    result = Counter()
    lines = [styles.strip_ansi(row) for row in rows]
    queued = False
    for index, line in enumerate(lines):
        if QUEUE.search(line):
            queued = True
            continue
        match = PROMPT.match(line)
        queue_match = re.match(r"^\s*↳\s?(.*)", line) if queued else None
        if not match and not queue_match:
            if line.strip() and not line.startswith("  "):
                queued = False
            continue
        kind = "queued" if queue_match else "submitted"
        text = queue_match.group(1) if queue_match else match.group(2)
        end = index + 1
        while end < len(lines) and lines[end].startswith("  ") and lines[end].strip():
            if FOOTER.match(lines[end]) or PROMPT.match(lines[end]) or re.match(r"^\s*↳", lines[end]):
                break
            text += "\n" + lines[end][2:].rstrip()
            end += 1
        following = next((row for row in lines[end:] if row.strip()), "")
        previous = next((row for row in reversed(lines[:index]) if row.strip()), "")
        # Full-screen redraw can archive an UNSENT input box in scrollback.
        # Its footer/rules still identify it as a composer, not a receipt.
        rule = lambda row: bool(row.strip()) and set(row.strip()) == {"─"}
        if kind == "submitted" and (FOOTER.match(following) or rule(following) or rule(previous)):
            continue
        result[(kind, normalized(text))] += 1
    return result


def paste_evidence(inp, payload):
    if inp.kind != "text":
        return False
    if normalized(inp.text) == normalized(payload):
        return True
    # Codex also collapses a whole paste into a character-count marker.
    # Require the entire input to be that marker: surrounding text is mixed.
    content = re.fullmatch(r"\[Pasted Content ([0-9]+) chars\]", inp.text, re.I)
    if content:
        # Terminal clients count either Unicode code points or UTF-16 units.
        counts = {str(len(payload)), str(len(payload.encode("utf-16-le")) // 2)}
        return content.group(1) in counts
    # A newly introduced collapsed marker is sufficient evidence of our paste.
    # Any visible text surrounding it must be consistent with this payload.
    matches = list(PASTE.finditer(inp.text))
    if len(matches) != 1:
        return False
    m = matches[0]
    before, after = normalized(inp.text[:m.start()]), normalized(inp.text[m.end():])
    wanted = normalized(payload)
    return (len(before) + len(after) < len(wanted)
            and wanted.startswith(before) and wanted.endswith(after))


class DeliveryError(Exception):
    def __init__(self, reason, code=2):
        self.reason, self.code = reason, code


class Tmux:
    def __init__(self, target, *, socket=None):
        resolver_env = None
        self.base = ["tmux"]
        if socket is not None:
            self.base += ["-S", socket]
            resolver_env = dict(os.environ, SPAWN_TMUX_SOCKET=socket)
            resolver_env.pop("SPAWN_TMUX_LABEL", None)
        elif os.environ.get("SPAWN_TMUX_SOCKET"):
            self.base += ["-S", os.environ["SPAWN_TMUX_SOCKET"]]
        elif os.environ.get("SPAWN_TMUX_LABEL"):
            self.base += ["-L", os.environ["SPAWN_TMUX_LABEL"]]
        elif os.environ.get("TMUX"):
            self.base += ["-S", os.environ["TMUX"].split(",")[0]]
        try:
            self.identity = subprocess.run(
                [str(Path(__file__).with_name("tmux-resolve.sh")), target],
                check=True, capture_output=True, text=True, timeout=5, env=resolver_env).stdout.strip()
        except subprocess.CalledProcessError as error:
            raise DeliveryError("missing-or-ambiguous-target") from error
        if not re.fullmatch(r"\d+:%\d+", self.identity):
            raise DeliveryError("invalid-target")
        self.pane = self.identity.split(":")[1]

    def run(self, *args):
        return subprocess.run(self.base + list(args), check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=5).stdout

    def frame(self):
        # Recheck coordinates around capture; discard frames that scroll/resize
        # while being sampled. No application internals are involved.
        fmt = "#{cursor_x}:#{cursor_y}:#{pane_width}:#{pane_height}:#{pane_in_mode}"
        before = self.run("display-message", "-p", "-t", self.pane, fmt).strip()
        history = self.run("capture-pane", "-e", "-p", "-t", self.pane, "-S", "-2000")
        after = self.run("display-message", "-p", "-t", self.pane, fmt).strip()
        values = before.split(":")
        if before != after or len(values) != 5 or values[-1] != "0":
            return Frame("", -1, -1)
        raw = "\n".join(history.splitlines()[-int(values[3]):]) + "\n"
        return Frame(raw, int(values[0]), int(values[1]), history)

    def keys(self, *keys):
        self.run("send-keys", "-t", self.pane, *keys)

    def type_command(self, text):
        self.run("send-keys", "-t", self.pane, "-l", "--", text)

    def paste(self, path):
        name = "tmux-message-" + uuid.uuid4().hex
        self.run("load-buffer", "-b", name, str(path))
        try:
            self.run("paste-buffer", "-p", "-r", "-d", "-t", self.pane, "-b", name)
        finally:
            try:
                self.run("delete-buffer", "-b", name)
            except subprocess.CalledProcessError:
                pass


def read_response_rows(pane, socket):
    """Read visible response rows above recognized input, never send keys.

    This is the supported Python observation API. Unknown, modal, or moving
    frames return None; target/read failures are sanitized DeliveryError.
    """
    if (not isinstance(pane, str) or not re.fullmatch(r"%\d+", pane)
            or not isinstance(socket, str) or not Path(socket).is_absolute()):
        raise DeliveryError("invalid-observation-target")
    try:
        frame = Tmux(pane, socket=socket).frame()
        inp = composer(frame)
        if inp.kind in {"unknown", "modal"}:
            return None
        return styles.strip_ansi(frame.raw).splitlines()[:inp.start]
    except (subprocess.SubprocessError, OSError, ValueError, TypeError, AttributeError, LookupError) as error:
        raise DeliveryError("observation-read-failed") from error


@contextmanager
def pane_lock(identity, timeout):
    # Share the compatibility sender's namespace. This lock coordinates senders,
    # not humans, and needs no registry or persistent per-message state.
    directory = Path(os.environ.get("CLAUDE_SEND_LOCK_DIR", f"/tmp/claude-send-locks-{os.getuid()}"))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise DeliveryError("unsafe-lock-directory")
    directory.chmod(0o700)
    path = directory / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DeliveryError("sender-busy")
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


class Sender:
    def __init__(self, tmux, options, payload, path, clock=time.monotonic, sleep=time.sleep):
        self.tmux, self.o, self.payload, self.path = tmux, options, payload, path
        self.clock, self.sleep = clock, sleep
        self.pasted = False
        self.enters = 0
        self.typed = getattr(options, "typed_command", False)
        if self.typed and getattr(options, "literal_exit", False):
            raise DeliveryError("conflicting-input-modes")
        if self.typed and (len(payload.encode("utf-8")) > 200 or payload != payload.rstrip() or payload.endswith(";")):
            raise DeliveryError("unsupported-typed-command")
        if self.typed and not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_-]*(?: [^\r\n\x00-\x1f\x7f]*)?", payload):
            raise DeliveryError("unsupported-typed-command")

    def input_evidence(self, inp):
        if self.typed:
            return inp.kind == "text" and inp.text == self.payload
        return paste_evidence(inp, self.payload)

    def sample(self):
        frame = self.tmux.frame()
        return frame, composer(frame)

    def ready(self):
        deadline = self.clock() + self.o.max_wait
        previous = None
        stable_since = self.clock()
        probed = False
        last_reason = "unsupported-layout"
        while self.clock() < deadline:
            frame, inp = self.sample()
            now = self.clock()
            fp = inp.fingerprint()
            if now >= deadline:
                break
            if previous is None:
                stable_since = now
            if previous is not None and fp != previous:
                # Text/cursor changes within a known input indicate typing.
                # A startup unknown -> empty transition is ordinary readiness.
                if inp.kind in {"text", "suggestion", "empty"} and previous[0] in {"text", "suggestion", "empty"}:
                    if not self.o.backoff:
                        raise DeliveryError("human-typing")
                    last_reason = "human-typing"
                stable_since = now
            previous = fp
            if inp.kind == "modal":
                raise DeliveryError("modal")
            if now - stable_since >= self.o.stable_wait:
                if inp.kind == "empty":
                    return frame, inp
                # Retirement's literal exit permits only Claude's canonical
                # idle suggestion at the input-start cursor, never draft text.
                if (getattr(self.o, "literal_exit", False) and self.payload == "/exit"
                        and inp.kind == "suggestion" and inp.harness == "claude"
                        and re.fullmatch(r'Try "[^\n]*"', inp.text)):
                    return frame, inp
                if inp.kind == "suggestion" and not probed:
                    # Ted-authorized probe: real draft text remains; a ghost
                    # suggestion should disappear. Never clear actual text.
                    self.tmux.keys("Space", "BSpace")
                    probed = True
                    previous = None
                    stable_since = self.clock()
                elif inp.kind in {"text", "suggestion"}:
                    last_reason = "existing-input"
                    if not self.o.backoff:
                        raise DeliveryError(last_reason)
            self.sleep(min(self.o.poll_interval, max(0, deadline - self.clock())))
        raise DeliveryError(last_reason)

    def deliver(self):
        before, inp = self.ready()
        baseline = records(before, inp)
        # One final sample catches input beginning just after the stable period.
        before, check = self.sample()
        if check.fingerprint() != inp.fingerprint():
            raise DeliveryError("input-changed-before-paste")
        baseline = records(before, check)
        literal = getattr(self.o, "literal_exit", False)
        if literal and (self.payload != "/exit" or check.harness != "claude"):
            raise DeliveryError("literal-exit-requires-claude-exit")
        resume_before = resume_records(before) if literal else Counter()
        compact_before = styles.strip_ansi(before.history or before.raw).count("• Context compacted")
        self.pasted = True  # A failed paste command can still have changed input.
        if literal:
            self.tmux.keys("-l", self.payload)
        elif self.typed:
            self.tmux.type_command(self.payload)
        else:
            self.tmux.paste(self.path)
        start = self.clock()
        deadline = start + self.o.render_wait + self.o.submit_delay + self.o.receipt_wait
        owned = None
        evidence_since = None
        last_enter = None
        input_disappeared = False
        accepted_forms = {normalized(self.payload)}
        while self.clock() < deadline:
            frame, current = self.sample()
            now = self.clock()
            if now >= deadline:
                break
            receipt = records(frame, current)
            if literal and self.enters == 1 and current.kind == "unknown":
                resumed = resume_records(frame)
                fresh = [session for session, count in resumed.items() if count > resume_before[session]]
                if fresh:
                    return {"status": "delivered", "receipt": "command", "command": "claude-exit",
                            "resume_session_id": fresh[0], "enters": 1, "transport": "literal"}
            if (self.payload == "/compact" and self.enters and current.kind == "empty"
                    and styles.strip_ansi(frame.history or frame.raw).count("• Context compacted") > compact_before):
                return {"status": "delivered", "receipt": "command", "enters": self.enters}
            # A collapsed receipt is usable only if the same marker was seen
            # in this attempt's input. Existing marker history is in baseline.
            for kind in ("submitted", "queued"):
                if not literal and self.enters and any(receipt[(kind, form)] > baseline[(kind, form)] for form in accepted_forms):
                    return {"status": "delivered", "receipt": kind, "enters": self.enters}
            if current.kind == "modal":
                raise DeliveryError("modal-after-paste", 3)
            if owned is not None and current.kind == "empty":
                input_disappeared = True
            evidence = self.input_evidence(current)
            if evidence:
                fp = current.fingerprint()
                if owned is not None and fp != owned and self.enters:
                    raise DeliveryError("input-changed-after-paste", 3)
                if owned != fp:
                    evidence_since = now
                owned = fp
                accepted_forms.add(normalized(current.text))
                due = start + self.o.submit_delay if last_enter is None else last_enter + self.o.retry_wait
                if (now >= due and now - evidence_since >= self.o.poll_interval
                        and self.enters < (1 if literal else 2) and not input_disappeared):
                    # Refresh receipt AND input immediately before each Enter.
                    confirm, live = self.sample()
                    fresh = records(confirm, live)
                    for kind in ("submitted", "queued"):
                        if not literal and self.enters and any(fresh[(kind, form)] > baseline[(kind, form)] for form in accepted_forms):
                            return {"status": "delivered", "receipt": kind, "enters": self.enters}
                    if live.fingerprint() != owned or not self.input_evidence(live):
                        raise DeliveryError("input-changed-before-enter", 3)
                    self.tmux.keys("Enter")
                    self.enters += 1
                    last_enter = self.clock()
            elif current.kind in {"text", "suggestion"} and (owned is not None or now - start >= self.o.render_wait):
                raise DeliveryError("mixed-input-after-paste", 3)
            elif owned is None and now - start >= self.o.render_wait:
                raise DeliveryError("paste-not-observed", 3)
            # Once input disappears, just wait for receipt. Never press Enter
            # into the next empty input, even if history rendering is delayed.
            self.sleep(min(self.o.poll_interval, max(0, deadline - self.clock())))
        raise DeliveryError("receipt-timeout", 3)


def options(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("target")
    p.add_argument("--prompt-file", "-f", type=Path)
    p.add_argument("--typed-command", action="store_true", help="type one single-line slash command with literal keys; retain all composer/receipt guards")
    for name, default in (("stable-wait", 3), ("max-wait", 10), ("submit-delay", 8),
                          ("render-wait", 3), ("receipt-wait", 15), ("retry-wait", 3),
                          ("poll-interval", 0.25)):
        p.add_argument("--" + name, type=float, default=default)
    p.add_argument("--literal-exit", action="store_true", help="type a bare Claude /exit literally, send at most one Enter, and verify a fresh resume notice")
    p.add_argument("--backoff", action="store_true", help="wait for stable empty input within --max-wait instead of exiting on typing/drafts")
    p.add_argument("message", nargs="?")
    o = p.parse_intermixed_args(argv)
    for name in ("stable_wait", "max_wait", "submit_delay", "render_wait", "receipt_wait", "retry_wait", "poll_interval"):
        value = getattr(o, name)
        maximum = math.inf if name == "receipt_wait" else 600
        if not math.isfinite(value) or (not 0 < value <= maximum and not (name == "submit_delay" and value == 0)):
            p.error(name.replace("_", "-") + " must be finite and positive (submit-delay may be zero); options except receipt-wait are capped at 600 seconds")
    if bool(o.prompt_file) == (o.message is not None):
        p.error("provide exactly one message or --prompt-file")
    if not o.target or o.target.startswith("-"):
        p.error("invalid target")
    return o


def main():
    o = options()
    try:
        payload = o.prompt_file.read_bytes().decode("utf-8") if o.prompt_file else o.message
        if o.literal_exit and payload != "/exit":
            raise ValueError("--literal-exit accepts only the bare /exit command")
        if not payload.strip() or any(unicodedata.category(c) == "Cc" and c != "\n" for c in payload):
            raise ValueError("message must be nonblank UTF-8 text without control characters other than newlines")
    except (OSError, UnicodeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    pending = Path(tempfile.mkdtemp(prefix="tmux-message-"))
    path = pending / "message"
    path.write_bytes(payload.encode("utf-8"))
    path.chmod(0o600)
    sender = None
    def interrupted(signum, _frame):
        raise DeliveryError("signal-" + str(signum), 128 + signum)
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        tmux = Tmux(o.target)
        sender = Sender(tmux, o, payload, path)
        with pane_lock(tmux.identity, o.max_wait):
            result = sender.deliver()
        result["pane"] = tmux.pane
        print(json.dumps(result))
        path.unlink()
        pending.rmdir()
        return 0
    except (DeliveryError, OSError, subprocess.SubprocessError) as error:
        pasted = sender is not None and sender.pasted
        code = error.code if isinstance(error, DeliveryError) else (3 if pasted else 2)
        reason = error.reason if isinstance(error, DeliveryError) else "tmux-operation-failed"
        result = {"status": "ambiguous" if pasted else "deferred", "reason": reason,
                  "pending_file": str(path), "needs_review": pasted or reason in {"unsupported-layout", "modal"},
                  "paste_attempted": pasted and not sender.typed,
                  "input_attempted": pasted, "input_method": "typed" if o.typed_command or o.literal_exit else "paste",
                  "enters": sender.enters if sender else 0}
        if sender:
            result["pane"] = sender.tmux.pane
        print(json.dumps(result), file=sys.stderr)
        return code


if __name__ == "__main__":
    sys.exit(main())
