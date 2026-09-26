#!/usr/bin/env python3
"""Return only a conservative Claude/Codex composer state, never screen text.

The caller verifies the shared Claude live-composer classifier and modal
detector; Codex uses the known prompt and footer. Raw visible rows preserve
cursor coordinates. Wrapped layouts the parser cannot compare exactly become
unknown. The helper never prints composer content.
"""
import pathlib
import sys
import unicodedata


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
    lines = screen.splitlines()
    if "Context " in screen and "›" in screen:
        return codex_state(lines, payload, cx, cy)
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
    # Claude currently paints a suggestion in an empty composer. Only accept
    # its explicit suggestion prefix as empty; a human can still race between
    # polls, and a typed literal suggestion is an unavoidable screen-only
    # ambiguity. The caller requires two stable observations and the cursor
    # check before relying on this state.
    if observed.startswith('Try "'):
        return "empty" if cy == idx and cx == 2 else "busy"
    expected_y = idx + payload.count("\n")
    expected_x = 2 + cell_width(payload.rsplit("\n", 1)[-1])
    return "own" if observed == wanted and (cy, cx) == (expected_y, expected_x) else "busy"


def codex_state(lines: list[str], payload: str, cx: int, cy: int) -> str:
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
    if observed == "Ask Codex to do anything":
        return "empty" if cy == idx and cx == 2 else "busy"
    expected_y = idx + payload.count("\n")
    expected_x = 2 + cell_width(payload.rsplit("\n", 1)[-1])
    return "own" if observed == payload.rstrip("\n") and (cy, cx) == (expected_y, expected_x) else "busy"


def main() -> int:
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
