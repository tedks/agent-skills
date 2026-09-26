#!/bin/bash
# Hermetic concurrency test for claude-send's per-target paste/submit lock.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR=$(mktemp -d /tmp/test-claude-send.XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir "$TEST_DIR/bin"
export FAKE_TMUX_LOG="$TEST_DIR/tmux.log"
export FAKE_BUFFER_DIR="$TEST_DIR/buffers"
export FAKE_SLEEP_LOG="$TEST_DIR/sleeps.log"
export FAKE_FLOCK_LOG="$TEST_DIR/flock.log"
mkdir "$FAKE_BUFFER_DIR"

cat > "$TEST_DIR/bin/tmux" <<'STUB'
#!/bin/bash
set -euo pipefail
while [[ "${1:-}" == -S || "${1:-}" == -L ]]; do shift 2; done
command="$1"
shift
case "$command" in
    display-message)
        printf '%s\n' '4242:%7'
        ;;
    load-buffer)
        while (($#)); do
            if [[ "$1" == -b ]]; then buffer="$2"; shift 2; else file="$1"; shift; fi
        done
        cp "$file" "$FAKE_BUFFER_DIR/$buffer"
        ;;
    paste-buffer)
        target=''
        buffer=''
        while (($#)); do
            case "$1" in
                -t) target="$2"; shift 2 ;;
                -b) buffer="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        if [[ "${FAKE_FAIL_PASTE:-0}" == 1 ]]; then exit 1; fi
        printf 'paste-start %s %s\n' "$target" "$(<"$FAKE_BUFFER_DIR/$buffer")" >> "$FAKE_TMUX_LOG"
        sleep 0.2
        printf 'paste-end %s\n' "$target" >> "$FAKE_TMUX_LOG"
        rm "$FAKE_BUFFER_DIR/$buffer"
        ;;
    delete-buffer) printf 'delete-buffer\n' >> "$FAKE_TMUX_LOG" ;;
    send-keys)
        [[ "${!#}" == Enter ]]
        printf 'enter\n' >> "$FAKE_TMUX_LOG"
        ;;
    *) echo "unexpected tmux command: $command" >&2; exit 64 ;;
esac
STUB
chmod +x "$TEST_DIR/bin/tmux"
cat > "$TEST_DIR/bin/flock" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_FLOCK_LOG"
if [[ "${FAKE_FAIL_FLOCK:-0}" == 1 ]]; then exit 1; fi
exec /usr/bin/flock "$@"
STUB
cat > "$TEST_DIR/bin/sleep" <<'STUB'
#!/bin/bash
if [[ "$1" == 0.2 ]]; then exec /usr/bin/sleep "$@"; fi
printf '%s\n' "$1" >> "$FAKE_SLEEP_LOG"
exec /usr/bin/sleep 0.01
STUB
chmod +x "$TEST_DIR/bin/flock" "$TEST_DIR/bin/sleep"
export PATH="$TEST_DIR/bin:$PATH"
export SPAWN_TMUX_SOCKET="$TEST_DIR/tmux.sock"

"$SCRIPT_DIR/claude-send.sh" test:agent alpha & first=$!
env -u SPAWN_TMUX_SOCKET SPAWN_TMUX_LABEL=personal \
    "$SCRIPT_DIR/claude-send.sh" test:7 --submit-delay 08 beta & second=$!
wait "$first"
wait "$second"

python3 - "$FAKE_TMUX_LOG" <<'PY'
from pathlib import Path
import sys

lines = Path(sys.argv[1]).read_text().splitlines()
assert len(lines) == 6, lines
for offset in (0, 3):
    start, end, enter = lines[offset:offset + 3]
    assert start.startswith("paste-start %7 "), lines
    assert end == "paste-end %7", lines
    assert enter == "enter", lines
    assert start.split()[-1] in {"alpha", "beta"}, lines
assert {lines[0].split()[-1], lines[3].split()[-1]} == {"alpha", "beta"}, lines
PY

[[ $(wc -l < "$FAKE_SLEEP_LOG") -eq 2 ]]
python3 - "$FAKE_SLEEP_LOG" <<'PY'
from pathlib import Path
import sys
assert Path(sys.argv[1]).read_text().splitlines() == ["8", "8"]
PY
python3 - "$FAKE_FLOCK_LOG" <<'PY'
from pathlib import Path
import sys
assert all(line.startswith("-w 90 ") for line in Path(sys.argv[1]).read_text().splitlines())
PY

: > "$FAKE_TMUX_LOG"
if FAKE_FAIL_PASTE=1 "$SCRIPT_DIR/claude-send.sh" test:agent should-fail 2>"$TEST_DIR/paste-error"; then
    echo "paste failure unexpectedly succeeded" >&2
    exit 1
fi
[[ $(grep -c '^delete-buffer$' "$FAKE_TMUX_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_TMUX_LOG") -eq 0 ]]
# A failed paste cleans up and releases the target lock.
"$SCRIPT_DIR/claude-send.sh" test:agent after-failure
[[ $(grep -c '^enter$' "$FAKE_TMUX_LOG") -eq 1 ]]
if FAKE_FAIL_FLOCK=1 "$SCRIPT_DIR/claude-send.sh" test:agent lock-fails 2>"$TEST_DIR/flock-error"; then
    echo "lock failure unexpectedly succeeded" >&2
    exit 1
fi
grep -q 'timed out waiting' "$TEST_DIR/flock-error"
echo "claude-send serialization test passed"
