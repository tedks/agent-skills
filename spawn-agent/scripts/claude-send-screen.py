#!/usr/bin/env python3
"""Return only a conservative Claude/Codex composer state, never screen text.

The caller verifies the shared Claude live-composer classifier and modal
detector; Codex uses the known prompt and footer. Raw visible rows preserve
cursor coordinates. Wrapped layouts the parser cannot compare exactly become
unknown. The helper never prints composer content.
"""
import pathlib
import re
import sys
import unicodedata

CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return CSI_RE.sub("", text)


def dim_span(raw_line: str, needle: str) -> bool:
    """Return true only when every visible character in needle has SGR dim."""
    chars = []
    dim_flags = []
    dim = False
    offset = 0
    for match in CSI_RE.finditer(raw_line):
        chunk = raw_line[offset : match.start()]
        chars.extend(chunk)
        dim_flags.extend([dim] * len(chunk))
        code = match.group(0)
        if code.endswith("m"):
            params = code[2:-1]
            try:
                values = [int(item) if item else 0 for item in params.split(";")] if params else [0]
            except ValueError:
                return False
            index = 0
            while index < len(values):
                value = values[index]
                if value in {0, 22}:
                    dim = False
                elif value == 2:
                    dim = True
                elif value in {38, 48, 58}:
                    # Extended foreground/background/underline colors have
                    # numeric payloads that can themselves contain 2. Skip
                    # the mode and its color components instead of treating
                    # them as attributes.
                    if index + 1 >= len(values):
                        return False
                    mode = values[index + 1]
                    if mode == 5 and index + 2 < len(values):
                        index += 2
                    elif mode == 2 and index + 4 < len(values):
                        index += 4
                    else:
                        return False
                index += 1
        offset = match.end()
    tail = raw_line[offset:]
    chars.extend(tail)
    dim_flags.extend([dim] * len(tail))
    visible = "".join(chars)
    start = visible.find(needle)
    return start >= 0 and all(dim_flags[start : start + len(needle)])


def cell_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char) or unicodedata.category(char) in {"Cf", "Mn", "Me"}:
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def state(screen: str, payload: str, cx: int, cy: int) -> str:
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in payload):
        return "unknown"
    lines = strip_ansi(screen).splitlines()
    if "Context " in screen and "›" in screen:
        return codex_state(lines, screen.splitlines(), payload, cx, cy)
    # Current Claude Code renders the live composer between two horizontal
    # rules, with no side gutters. Require that exact frame around the last
    # prompt marker; a transcript line containing ❯ is not sufficient.
    rules = [i for i, line in enumerate(lines) if line.strip() and set(line.strip()) == {"─"}]
    prompts = [i for i, line in enumerate(lines) if "❯" in line]
    if not prompts:
        return "unknown"
    idx = prompts[-1]
    before = [i for i in rules if i < idx]
    after = [i for i in rules if i > idx]
    if not before or not after:
        return "unknown"
    top, bottom = before[-1], after[0]
    if top >= idx or bottom <= idx:
        return "unknown"
    line = lines[idx]
    fragment = line.split("❯", 1)[1]
    if fragment.startswith(("\u00a0", " ")):
        fragment = fragment[1:]
    parts = [fragment]
    for row in lines[idx + 1 : bottom]:
        if "❯" in row:
            return "unknown"
        # Claude's hard-newline continuations are indented two spaces. Soft
        # wrapping changes the measured cursor row and fails exact ownership.
        parts.append(row[2:] if row.startswith("  ") else row)
    observed = "\n".join(part.rstrip() for part in parts)
    # Terminal padding is discarded, but internal spaces and line breaks
    # remain exact. A wrapped visual row without the known hard-newline indent
    # will not compare as the payload and therefore fails closed.
    wanted = payload.rstrip("\n")
    observed = observed.rstrip("\n")
    if observed == "":
        return "empty" if cy == idx and cx == 2 else "busy"
    expected_y = idx + payload.count("\n")
    expected_x = 2 + cell_width(payload.rsplit("\n", 1)[-1])
    if observed == wanted and (cy, cx) == (expected_y, expected_x):
        return "own"
    # Claude paints a suggestion in an empty composer without a distinct style.
    # A human can type that exact text and Home the cursor, producing the same
    # capture. Therefore a suggestion is never evidence of an empty composer.
    if observed.startswith('Try "'):
        return "unknown"
    return "busy"


def codex_state(lines: list[str], raw_lines: list[str], payload: str, cx: int, cy: int) -> str:
    # Match the live prompt/context/footer shape. A Working row is allowed
    # between prompt and context because generation can continue while the
    # composer stays empty. Known shortcut, agent, and warning footer text may follow.
    matches = [i for i, line in enumerate(lines) if "›" in line]
    if not matches:
        return "unknown"
    idx = matches[-1]
    contexts = [i for i, line in enumerate(lines) if i > idx and "Context " in line]
    if not contexts:
        return "unknown"
    footer = contexts[0]
    if footer - idx > 2:
        return "unknown"
    if any(line.strip() and not ("? for shortcuts" in line or "warning" in line.lower() or "agents" in line.lower()) for line in lines[footer + 1 :]):
        return "unknown"
    fragment = lines[idx].split("›", 1)[1]
    if fragment.startswith(" "):
        fragment = fragment[1:]
    continuations = []
    for row in lines[idx + 1 : footer]:
        if not row.strip():
            continuations.append("")
        elif row.startswith("  "):
            continuations.append(row[2:].rstrip())
        elif row.startswith("Working ("):
            continue
        else:
            return "unknown"
    observed = "\n".join([fragment.rstrip(), *continuations]).rstrip("\n")
    if observed == "":
        return "empty" if cy == idx and cx == 2 else "busy"
    expected_y = idx + payload.count("\n")
    expected_x = 2 + cell_width(payload.rsplit("\n", 1)[-1])
    if observed == payload.rstrip("\n") and (cy, cx) == (expected_y, expected_x):
        return "own"
    if observed == "Ask Codex to do anything":
        if cy == idx and cx == 2 and idx < len(raw_lines) and dim_span(raw_lines[idx], observed):
            return "empty"
        return "busy"
    return "busy"


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--strip-ansi":
        sys.stdout.write(strip_ansi(sys.stdin.read()))
        return 0
    if len(sys.argv) != 4:
        return 64
    try:
        payload = pathlib.Path(sys.argv[1]).read_bytes().decode("utf-8")
        screen = sys.stdin.read()
    except (OSError, UnicodeError):
        print("unknown")
        return 0
    try:
        cx, cy = int(sys.argv[2]), int(sys.argv[3])
    except (IndexError, ValueError):
        print("unknown")
        return 0
    print(state(screen, payload, cx, cy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
