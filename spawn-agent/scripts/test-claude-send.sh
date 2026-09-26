#!/bin/bash
# Hermetic concurrency test for claude-send's per-target paste/submit lock.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR=$(mktemp -d /tmp/test-claude-send.XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir "$TEST_DIR/bin"
export FAKE_TMUX_LOG="$TEST_DIR/tmux.log"
export FAKE_BUFFER_DIR="$TEST_DIR/buffers"
mkdir "$FAKE_BUFFER_DIR"

cat > "$TEST_DIR/bin/tmux" <<'STUB'
#!/bin/bash
set -euo pipefail
while [[ "${1:-}" == -S || "${1:-}" == -L ]]; do shift 2; done
command="$1"
shift
case "$command" in
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
        printf 'paste-start %s %s\n' "$target" "$(<"$FAKE_BUFFER_DIR/$buffer")" >> "$FAKE_TMUX_LOG"
        sleep 0.2
        printf 'paste-end %s\n' "$target" >> "$FAKE_TMUX_LOG"
        rm "$FAKE_BUFFER_DIR/$buffer"
        ;;
    delete-buffer) ;;
    send-keys)
        printf 'enter\n' >> "$FAKE_TMUX_LOG"
        ;;
    *) echo "unexpected tmux command: $command" >&2; exit 64 ;;
esac
STUB
chmod +x "$TEST_DIR/bin/tmux"
export PATH="$TEST_DIR/bin:$PATH"
export SPAWN_TMUX_SOCKET="$TEST_DIR/tmux.sock"

"$SCRIPT_DIR/claude-send.sh" test:agent alpha & first=$!
"$SCRIPT_DIR/claude-send.sh" test:agent beta & second=$!
wait "$first"
wait "$second"

python3 - "$FAKE_TMUX_LOG" <<'PY'
from pathlib import Path
import sys

lines = Path(sys.argv[1]).read_text().splitlines()
assert len(lines) == 6, lines
for offset in (0, 3):
    start, end, enter = lines[offset:offset + 3]
    assert start.startswith("paste-start test:agent "), lines
    assert end == "paste-end test:agent", lines
    assert enter == "enter", lines
    assert start.split()[-1] in {"alpha", "beta"}, lines
assert {lines[0].split()[-1], lines[3].split()[-1]} == {"alpha", "beta"}, lines
PY
echo "claude-send serialization test passed"
