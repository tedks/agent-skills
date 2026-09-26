#!/bin/bash
# Fake-tmux regression tests for human-composer-safe claude-send delivery.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FIXTURES="$SCRIPT_DIR/fixtures/claude-send"
TEST_DIR=$(mktemp -d /tmp/test-claude-send.XXXXXX)
TEST_REAL_PYTHON3=$(command -v python3)
export TEST_REAL_PYTHON3
cleanup_test() {
  local rc=$?
  if ((rc == 0)); then
    rm -rf "$TEST_DIR"
  else
    echo "fake-tmux diagnostics preserved at $TEST_DIR" >&2
    if [[ -s "$FAKE_STATE/last-screen" && -s "$FAKE_STATE/last-cursor" ]]; then
      printf 'fake tmux command/exit trace:\n' >&2
      cat "$FAKE_STATE/tmux-trace" >&2 2>/dev/null || true
      printf 'fake parser command/exit trace:\n' >&2
      cat "$FAKE_STATE/parser-trace" >&2 2>/dev/null || true
      if [[ -f "$FAKE_STATE/last-parser-input" ]]; then
        printf 'last parser input:\n' >&2; cat "$FAKE_STATE/last-parser-input" >&2
      fi
      printf 'last fake pane mode: ' >&2; cat "$FAKE_STATE/mode" >&2
      printf 'last fake cursor: '; cat "$FAKE_STATE/last-cursor" >&2
      printf 'last fake screen:\n' >&2; cat "$FAKE_STATE/last-screen" >&2
      IFS=: read -r diag_x diag_y < "$FAKE_STATE/last-cursor"
      printf 'classifier result for synthetic payload: ' >&2
      python3 "$SCRIPT_DIR/claude-send-screen.py" "$TEST_DIR/payload" "$diag_x" "$diag_y" \
        < "$FAKE_STATE/last-screen" >&2 || true
    fi
  fi
  return "$rc"
}
trap cleanup_test EXIT
mkdir -p "$TEST_DIR/bin" "$TEST_DIR/state" "$TEST_DIR/buffers"
mkdir -p "$TEST_DIR/runtime"
export FIXTURE_CURRENT="$FIXTURES/codex-context-queue.ansi"
export FAKE_STATE="$TEST_DIR/state"
export FAKE_LOG="$TEST_DIR/tmux.log"
export FAKE_BUFFER_DIR="$TEST_DIR/buffers"
export XDG_STATE_HOME="$TEST_DIR/state-home"
export XDG_RUNTIME_DIR="$TEST_DIR/runtime"
export CLAUDE_SEND_LOCK_DIR="$TEST_DIR/locks"
export SPAWN_TMUX_SOCKET="$TEST_DIR/tmux.sock"
export PATH="$TEST_DIR/bin:$PATH"

cat > "$TEST_DIR/bin/python3" <<'STUB'
#!/bin/bash
if [[ "${1:-}" == */agent-message.py && "${2:-}" == guard ]]; then
  n=0; [[ -s "$FAKE_STATE/guards" ]] && n=$(<"$FAKE_STATE/guards")
  n=$((n + 1)); printf '%s' "$n" > "$FAKE_STATE/guards"
  if [[ "${FAKE_ATTEMPT_UNKNOWN:-0}" == 1 ]]; then exit 3; fi
  if [[ "${FAKE_RECEIPT_AFTER_GUARDS:-0}" -gt 0 && "$n" -ge "$FAKE_RECEIPT_AFTER_GUARDS" ]]; then
    printf 'received\n'
  else printf 'missing\n'; fi
  exit 0
fi
if [[ "${FAKE_FAIL_CLOCK_BEFORE_PASTE:-0}" == 1 && "$*" == *monotonic_ns* && ! -s "$FAKE_STATE/pasted" ]]; then
  printf 'injected monotonic clock subprocess failure\n' >&2
  exit 1
fi
if [[ "${1:-}" == */claude-send-screen.py ]]; then
  input=$(mktemp "$FAKE_STATE/parser-input.XXXXXX")
  output=$(mktemp "$FAKE_STATE/parser-output.XXXXXX")
  errors=$(mktemp "$FAKE_STATE/parser-errors.XXXXXX")
  cat > "$input"
  if [[ "${FAKE_FAIL_PARSER_AFTER_PASTE:-0}" == 1 && -s "$FAKE_STATE/pasted" ]]; then
    printf 'injected classifier subprocess failure\n' > "$errors"
    rc=64
  else
    "$TEST_REAL_PYTHON3" "$@" < "$input" > "$output" 2> "$errors"
    rc=$?
  fi
  cp "$input" "$FAKE_STATE/last-parser-input"
  printf 'exit=%s args=%q output=%q stderr=%q\n' "$rc" "$*" "$(<"$output")" "$(<"$errors")" >> "$FAKE_STATE/parser-trace"
  cat "$output"
  cat "$errors" >&2
  exit "$rc"
fi
exec "$TEST_REAL_PYTHON3" "$@"
STUB

cat > "$TEST_DIR/bin/tmux" <<'STUB'
#!/bin/bash
set -euo pipefail
while [[ "${1:-}" == -S || "${1:-}" == -L ]]; do shift 2; done
cmd=$1; shift
original_args=$*
record_tmux_exit() {
  local rc=$?
  printf 'exit=%s command=%q args=%q\n' "$rc" "$cmd" "$original_args" >> "$FAKE_STATE/tmux-trace"
}
trap record_tmux_exit EXIT
printf '%s\n' "$cmd" >> "$FAKE_STATE/tmux-calls"
case "$cmd" in
  display-message)
    if [[ "${*: -1}" == '#{pid}:#{pane_id}' ]]; then printf '4242:%%7\n'
    elif [[ "${*: -1}" == '#{cursor_x}:#{cursor_y}' ]]; then
      if [[ "$(<"$FAKE_STATE/mode")" == busycontext && -s "$FAKE_STATE/pasted" ]]; then cursor='57:2'
      elif [[ "$(<"$FAKE_STATE/mode")" == busycontext ]]; then cursor='2:2'
      elif [[ "$(<"$FAKE_STATE/mode")" == busyqueue && -s "$FAKE_STATE/pasted" ]]; then cursor='37:27'
      elif [[ "$(<"$FAKE_STATE/mode")" == busyqueue ]]; then cursor='2:27'
      elif [[ "$(<"$FAKE_STATE/mode")" == persistent-empty-after-paste && -s "$FAKE_STATE/pasted" ]]; then cursor='2:1'
      elif [[ "$(<"$FAKE_STATE/mode")" == render-stale-empty && -s "$FAKE_STATE/pasted" && $(<"$FAKE_STATE/captures") -eq 4 ]]; then cursor='2:1'
      elif [[ -s "$FAKE_STATE/composer" ]]; then
        cursor=$(python3 - "$FAKE_STATE/composer" <<'PY'
from pathlib import Path
import sys
rows = Path(sys.argv[1]).read_text().splitlines()
print(f"{2 + len(rows[-1])}:{1 + len(rows) - 1}")
PY
        )
      elif [[ "$(<"$FAKE_STATE/mode")" == streaming ]]; then cursor='2:2'
      else cursor='2:1'; fi
      printf '%s\n' "$cursor" > "$FAKE_STATE/last-cursor"
      printf '%s\n' "$cursor"
    else printf '0\n'; fi
    ;;
  capture-pane)
    n=0; [[ -f "$FAKE_STATE/captures" ]] && n=$(<"$FAKE_STATE/captures")
    n=$((n + 1)); printf '%s' "$n" > "$FAKE_STATE/captures"
    mode=$(<"$FAKE_STATE/mode")
    if [[ "$mode" == interrupt-before-paste && $n -eq 1 ]]; then
      while [[ ! -s "$FAKE_STATE/sender-pid" ]]; do /usr/bin/sleep 0.01; done
      kill -TERM "$(<"$FAKE_STATE/sender-pid")"
      exit 143
    fi
    if [[ "$mode" == slow-first-own && $n -eq 4 ]]; then /usr/bin/sleep 1.2; fi
    if [[ "$mode" == slow-third-empty && $n -eq 3 ]]; then /usr/bin/sleep 1.2; fi
    if [[ "$mode" == slow-second-empty && $n -eq 2 ]]; then /usr/bin/sleep 1.2; fi
    if [[ "$mode" == human-before-paste && $n -ge 3 ]]; then
      printf 'human text before paste\n' > "$FAKE_STATE/composer"
    fi
    if [[ "$mode" == render-unknown-human && -s "$FAKE_STATE/pasted" && $n -ge 5 ]]; then
      printf 'human mixed input\n' > "$FAKE_STATE/composer"
    fi
    if [[ "$mode" == render-late-unknown && -s "$FAKE_STATE/pasted" && $n -eq 4 ]]; then /usr/bin/sleep 1.2; fi
    if [[ "$mode" == human-during-delay && -s "$FAKE_STATE/pasted" && $n -ge 5 ]]; then
      printf 'a human began typing\n' > "$FAKE_STATE/composer"
    fi
    if [[ "$mode" == busycontext ]]; then
      if [[ -s "$FAKE_STATE/pasted" ]]; then
        cat "$FIXTURE_CURRENT" > "$FAKE_STATE/last-screen"
      else
        sed 's/S short control: reply S-BUSY-1 after the current task.//' "$FIXTURE_CURRENT" > "$FAKE_STATE/last-screen"
      fi
    elif [[ "$mode" == busyqueue ]]; then
      if [[ -s "$FAKE_STATE/pasted" ]]; then
        cat "$FAKE_STATE/busyqueue" > "$FAKE_STATE/last-screen"
      else
        sed 's/Reply with exactly P141-QUEUED-TEST//' "$FAKE_STATE/busyqueue" > "$FAKE_STATE/last-screen"
      fi
    elif [[ -s "$FAKE_STATE/pasted" && "$mode" == render-stale-empty && $n -eq 4 ]]; then
      cat "$FAKE_STATE/empty" > "$FAKE_STATE/last-screen"
    elif [[ -s "$FAKE_STATE/pasted" && "$mode" == persistent-empty-after-paste ]]; then
      cat "$FAKE_STATE/empty" > "$FAKE_STATE/last-screen"
    elif [[ -s "$FAKE_STATE/pasted" && ( "$mode" == unknown-after-paste || "$mode" == unknown-after-own && $n -ge 5 || ( "$mode" == render-unknown || "$mode" == render-late-unknown || "$mode" == render-unknown-human ) && $n -eq 4 ) ]]; then
      printf 'Starting Claude Code\n' > "$FAKE_STATE/last-screen"
    else
    case "$mode" in
      modal) cat "$FAKE_STATE/modal" ;;
      unknown) printf 'Starting Claude Code\n';;
      *)
        if [[ -s "$FAKE_STATE/composer" ]]; then
          python3 - "$FAKE_STATE/composer" <<'PY'
from pathlib import Path
import sys
rows = Path(sys.argv[1]).read_text().splitlines()
print('─' * 40)
print('❯\u00a0' + (rows[0] if rows else ''))
for row in rows[1:]: print('  ' + row)
print('─' * 40)
PY
        elif [[ "$mode" == human || "$mode" == slash ]]; then
          if [[ "$mode" == slash ]]; then printf '────────────────────────────────────────\n❯ /compact\n────────────────────────────────────────\n'
          else printf '────────────────────────────────────────\n❯ human draft\n────────────────────────────────────────\n'; fi
        elif [[ "$mode" == streaming ]]; then
          printf 'Assistant is thinking…\n────────────────────────────────────────\n❯ \n────────────────────────────────────────\n'
        else cat "$FAKE_STATE/empty"; fi
        ;;
    esac > "$FAKE_STATE/last-screen"
    fi
    cat "$FAKE_STATE/last-screen"
    ;;
  load-buffer)
    while (($#)); do if [[ "$1" == -b ]]; then b=$2; shift 2; else f=$1; shift; fi; done
    cp "$f" "$FAKE_BUFFER_DIR/$b" ;;
  paste-buffer)
    b=''; while (($#)); do if [[ "$1" == -b ]]; then b=$2; shift 2; else shift; fi; done
    printf 'paste-start\n' >> "$FAKE_LOG"
    python3 -c 'import time; print(time.monotonic_ns())' >> "$FAKE_STATE/times"
    if [[ "${FAKE_FAIL_PASTE:-0}" == 1 ]]; then exit 1; fi
    cp "$FAKE_BUFFER_DIR/$b" "$FAKE_STATE/composer"
    printf 'pasted\n' > "$FAKE_STATE/pasted"
    if [[ "${FAKE_INTERRUPT_AFTER_PASTE:-0}" == 1 ]]; then
      kill -TERM "$PPID"
      exit 143
    fi
    sleep 0.05
    printf 'paste-end\n' >> "$FAKE_LOG"
    rm -f "$FAKE_BUFFER_DIR/$b" ;;
  delete-buffer) printf 'delete-buffer\n' >> "$FAKE_LOG" ;;
  send-keys)
    [[ "${!#}" == Enter ]]
    printf 'enter\n' >> "$FAKE_LOG"
    if [[ "${FAKE_IGNORE_FIRST_ENTER:-0}" == 1 && $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]; then exit 0; fi
    python3 -c 'import time; print(time.monotonic_ns())' >> "$FAKE_STATE/times"
    cp "$FAKE_STATE/composer" "$FAKE_STATE/submitted"
    : > "$FAKE_STATE/composer"
    ;;
  *) echo "unexpected tmux command: $cmd" >&2; exit 64 ;;
esac
STUB
cat > "$TEST_DIR/bin/sleep" <<'STUB'
#!/bin/bash
printf '%s\n' "$1" >> "$FAKE_STATE/sleeps"
if [[ "${FAKE_REAL_SLEEP:-0}" == 1 ]]; then exec /usr/bin/sleep "$1"; fi
exec /usr/bin/sleep 0.01
STUB
cat > "$TEST_DIR/bin/flock" <<'STUB'
#!/bin/bash
if [[ "${FAKE_FAIL_FLOCK:-0}" == 1 ]]; then exit 1; fi
exec /usr/bin/flock "$@"
STUB
chmod +x "$TEST_DIR/bin/python3" "$TEST_DIR/bin/tmux" "$TEST_DIR/bin/sleep" "$TEST_DIR/bin/flock"

reset_case() {
  : > "$FAKE_LOG"
  : > "$FAKE_STATE/composer"
  : > "$FAKE_STATE/pasted"
  : > "$FAKE_STATE/captures"
  : > "$FAKE_STATE/sleeps"
  : > "$FAKE_STATE/times"
  : > "$FAKE_STATE/tmux-calls"
  : > "$FAKE_STATE/tmux-trace"
  : > "$FAKE_STATE/parser-trace"
  : > "$FAKE_STATE/sender-pid"
  : > "$FAKE_STATE/guards"
  printf '%s\n' "${1:-empty}" > "$FAKE_STATE/mode"
  cp "$FIXTURES/claude-blank.ansi" "$FAKE_STATE/empty"
  cp "$FIXTURES/modal.txt" "$FAKE_STATE/modal"
  cp "$FIXTURES/codex-generating-queue.ansi" "$FAKE_STATE/busyqueue"
}
run_sender() {
  if [[ "$(<"$FAKE_STATE/mode")" == interrupt-before-paste ]]; then
    bash "$SCRIPT_DIR/claude-send.sh" "$@" &
    local sender_pid=$!
    printf '%s' "$sender_pid" > "$FAKE_STATE/sender-pid"
    wait "$sender_pid"
  else
    bash "$SCRIPT_DIR/claude-send.sh" "$@"
  fi
}
run_sender_without_xdg_runtime() { env -u XDG_RUNTIME_DIR bash "$SCRIPT_DIR/claude-send.sh" "$@"; }
assert_pending() {
  local status=$1 output=$2 path
  grep -q "status=$status" "$output"
  path=$(sed -n 's/.*pending_file=//p' "$output" | tail -n1)
  [[ -n "$path" && -f "$path" && $(stat -c %a "$(dirname "$path")") == 700 && $(stat -c %a "$path") == 600 ]]
  printf '%s' "$path"
}

# The live Claude frame has a suggestion in an otherwise empty composer.
prompt_file="$TEST_DIR/payload"
printf 'alpha\nbeta' > "$prompt_file"

# Invalid byte sequences, control characters, blank content, and trailing
# whitespace are rejected before any tmux operation, so they cannot be pasted.
python3 - "$TEST_DIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
payloads = {
    'malformed-utf8': b'valid then \xff',
    'carriage-return': b'left\rright',
    'delete': b'left\x7fright',
    'c1-control': b'left\xc2\x85right',
    'blank': b' \n\t\n',
    'trailing-space': b'valid text \n',
}
for name, content in payloads.items():
    (root / name).write_bytes(content)
PY
for invalid in malformed-utf8 carriage-return delete c1-control blank trailing-space; do
  reset_case empty
  if run_sender target --submit-delay 0 --max-wait 1 --poll-interval 0.1 --prompt-file "$TEST_DIR/$invalid" 2>"$TEST_DIR/$invalid.err"; then
    echo "FAIL: invalid $invalid payload was accepted" >&2; exit 1
  fi
  [[ ! -s "$FAKE_STATE/tmux-calls" ]]
  [[ $(grep -c '^paste-start$' "$FAKE_LOG" || true) -eq 0 ]]
done

# Numeric inputs too large for the documented ranges are rejected before the
# sender attempts to resolve or mutate a pane.
for option in --submit-delay --render-wait --max-wait --poll-interval; do
  reset_case empty
  if run_sender target "$option" 999999999999999999999 --prompt-file "$prompt_file" 2>"$TEST_DIR/numeric.err"; then
    echo "FAIL: overflowing $option was accepted" >&2; exit 1
  fi
  [[ ! -s "$FAKE_STATE/tmux-calls" ]]
done
reset_case empty
if run_sender target --render-wait 0 --prompt-file "$prompt_file" 2>"$TEST_DIR/zero-render-wait.err"; then
  echo 'FAIL: zero render budget was accepted' >&2; exit 1
fi
[[ ! -s "$FAKE_STATE/tmux-calls" ]]
reset_case empty
if run_sender target --max-wait 0 --prompt-file "$prompt_file" 2>"$TEST_DIR/zero-max-wait.err"; then
  echo 'FAIL: zero max-wait was accepted' >&2; exit 1
fi
[[ ! -s "$FAKE_STATE/tmux-calls" ]]
python3 - "$SCRIPT_DIR/claude-send-screen.py" "$FIXTURES" "$prompt_file" <<'PY'
from pathlib import Path
import subprocess, sys
parser, fixtures, payload = sys.argv[1:]
def classify(frame, text, x='2', y='1'):
    Path(payload).write_text(text)
    return subprocess.check_output([sys.executable, parser, payload, x, y], input=(Path(fixtures) / frame).read_bytes()).decode().strip()
assert classify('claude-blank.ansi', 'outgoing') == 'empty'
assert classify('multiline.txt', 'first harmless line\nsecond harmless line', str(2 + len('second harmless line')), '2') == 'own'
assert classify('multiline.txt', 'different') == 'busy'
assert classify('modal.txt', 'outgoing') == 'unknown'
assert classify('claude-blank.ansi', 'different') == 'empty'
codex = ('\n' * 26 + '\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m\nWorking (12s • esc to interrupt)\n'
         '  GPT-6-Astra · Context 100% left · disposable\n'
         '  ← for agents · ? for shortcuts\n')
Path(payload).write_text('unused')
assert subprocess.check_output([sys.executable, parser, payload, '2', '26'], input=codex.encode()).decode().strip() == 'empty'
Path(payload).write_text('harmless draft')
codex_own = codex.replace('Ask Codex to do anything', 'harmless draft').replace('Working (12s • esc to interrupt)\n', '')
assert subprocess.check_output([sys.executable, parser, payload, '16', '26'], input=codex_own.encode()).decode().strip() == 'own'

def classify_fixture(name, text, x, y):
    Path(payload).write_text(text)
    return subprocess.check_output(
        [sys.executable, parser, payload, str(x), str(y)],
        input=(Path(fixtures) / name).read_bytes(),
    ).decode().strip()
assert classify_fixture('claude-blank.ansi', 'outgoing', 2, 1) == 'empty'
empty_suggestion = (Path(fixtures) / 'claude-suggestion-empty.ansi').read_bytes()
typed_home_suggestion = (Path(fixtures) / 'claude-suggestion-typed-home.ansi').read_bytes()
assert empty_suggestion == typed_home_suggestion
# Claude emits no ANSI style distinction for this exact cursor-home collision;
# both frames must be deferred as occupied after transport updates.
assert classify_fixture('claude-suggestion-empty.ansi', 'outgoing', 2, 1) == 'unknown'
assert classify_fixture('claude-suggestion-typed-home.ansi', 'outgoing', 2, 1) == 'unknown'
assert classify_fixture('claude-try-prefix-typed.ansi', 'outgoing', 16, 1) == 'unknown'
assert classify_fixture('codex-empty-dim.ansi', 'outgoing', 2, 26) == 'empty'
assert classify_fixture('codex-typed-home.ansi', 'outgoing', 2, 26) == 'busy'
queue_payload = 'Reply with exactly P141-QUEUED-TEST'
queue_screen = (Path(fixtures) / 'codex-generating-queue.ansi').read_bytes()
queue_empty = queue_screen.replace(queue_payload.encode(), b'', 1)
Path(payload).write_text(queue_payload)
assert subprocess.check_output(
    [sys.executable, parser, payload, '2', '27'], input=queue_empty
).decode().strip() == 'empty'
assert subprocess.check_output(
    [sys.executable, parser, payload, '3', '27'], input=queue_empty
).decode().strip() == 'busy'
Path(payload).write_text(queue_payload + '\n')
assert subprocess.check_output(
    [sys.executable, parser, payload, '37', '27'], input=queue_screen
).decode().strip() == 'unknown'
Path(payload).write_text(queue_payload)
assert Path(fixtures, 'codex-generating-queue.cursor').read_text().strip() == '37:27'
assert subprocess.check_output(
    [sys.executable, parser, payload, '37', '27'], input=queue_screen
).decode().strip() == 'own'
unstyled_queue_hint = queue_screen.replace(b'\x1b[1mtab\x1b[0m', b'tab', 1)
assert subprocess.check_output(
    [sys.executable, parser, payload, '37', '27'], input=unstyled_queue_hint
).decode().strip() == 'unknown'
Path(payload).write_text('Reply with a different harmless payload')
assert subprocess.check_output(
    [sys.executable, parser, payload, '37', '27'], input=queue_screen
).decode().strip() == 'busy'
Path(payload).write_text(queue_payload)
assert subprocess.check_output(
    [sys.executable, parser, payload, '36', '27'], input=queue_screen
).decode().strip() == 'busy'
assert subprocess.check_output(
    [sys.executable, parser, payload, '37', '26'], input=queue_screen
).decode().strip() == 'busy'
extra_queue_row = queue_screen.replace(
    b'  \x1b[1mtab\x1b[0m to queue message',
    b'  unrelated app row\n  \x1b[1mtab\x1b[0m to queue message', 1,
)
assert subprocess.check_output(
    [sys.executable, parser, payload, '37', '27'], input=extra_queue_row
).decode().strip() == 'unknown'
# Measured current Codex separate model/context and queue-hint footer.
current = (Path(fixtures) / 'codex-context-queue.ansi').read_bytes()
text = 'S short control: reply S-BUSY-1 after the current task.'
x, y = map(int, Path(fixtures, 'codex-context-queue.cursor').read_text().split(':'))
def current_state(frame, wanted=text, px=x, py=y):
    Path(payload).write_text(wanted)
    return subprocess.check_output([sys.executable, parser, payload, str(px), str(py)], input=frame).decode().strip()
assert current_state(current) == 'own'
assert current_state(current, px=x-1) == 'busy'
assert current_state(current, py=y-1) == 'busy'
assert current_state(current, wanted=text+'\n') == 'unknown'
assert current_state(current.replace(text.encode(), b''), px=2) == 'empty'
assert current_state(current.replace(text.encode(), b'human draft'), px=13) == 'busy'
assert current_state(current.replace(text.encode(), (text+' HUMAN').encode()), px=x+6) == 'busy'
assert current_state(current.replace(b'\x1b[1mtab\x1b[0m', b'tab')) == 'unknown'
assert current_state(current+b'  unrelated modal\n') == 'unknown'
assert current_state(current.replace(b'  GPT-6-Sol', b'  arbitrary footer\n  GPT-6-Sol')) == 'unknown'
assert current_state(current.replace(text.encode(), b'Ask Codex to do anything'), px=2) == 'busy'
assert current_state(current.replace(text.encode(), b'\x1b[2mAsk Codex to do anything\x1b[0m'), px=2) == 'empty'
codex_dim = (Path(fixtures) / 'codex-empty-dim.ansi').read_text()
for color_sgr in ('\x1b[38;5;2m', '\x1b[38;2;2;2;2m', '\x1b[38:2:2:2:2m'):
    color_only = codex_dim.replace('\x1b[2mAsk Codex to do anything', color_sgr + 'Ask Codex to do anything')
    assert subprocess.check_output(
        [sys.executable, parser, payload, '2', '26'], input=color_only.encode()
    ).decode().strip() == 'busy'
PY
printf 'alpha\nbeta' > "$prompt_file"

# A measured Codex busy-queue composer accepts one exact short payload once.
reset_case busyqueue
queue_prompt="$TEST_DIR/queue-prompt"
printf 'Reply with exactly P141-QUEUED-TEST' > "$queue_prompt"
out=$(run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$queue_prompt")
[[ "$out" == 'status=sent receipt=unverified' ]]
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]
[[ $(<"$FAKE_STATE/submitted") == 'Reply with exactly P141-QUEUED-TEST' ]]
[[ ! -s "$FAKE_STATE/composer" ]]

# The current separate model/context queue footer works end-to-end too.
reset_case busycontext
printf 'S short control: reply S-BUSY-1 after the current task.' > "$queue_prompt"
out=$(run_sender target --submit-delay 0 --render-wait 2 --max-wait 3 --poll-interval 0.1 --prompt-file "$queue_prompt")
[[ "$out" == 'status=sent receipt=unverified' ]]
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]
cmp "$queue_prompt" "$FAKE_STATE/submitted"

# Once ownership was visible, an unknown frame stops even with a large budget.
reset_case unknown-after-own
if run_sender target --submit-delay 8 --render-wait 30 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/ownership-loss.err"; then
  echo 'FAIL: unknown frame after ownership was submitted' >&2; exit 1
else rc=$?; [[ $rc -eq 3 ]]; fi
grep -q 'reason=post-paste-unknown' "$TEST_DIR/ownership-loss.err"
pending=$(assert_pending ambiguous "$TEST_DIR/ownership-loss.err")
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# Reporting can fail after Enter: retain as ambiguous, never safe-to-retry deferred.
reset_case empty
if run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" >/dev/full 2>"$TEST_DIR/report-failure.err"; then
  echo 'FAIL: failed success reporting returned success' >&2; exit 1
else rc=$?; [[ $rc -eq 1 ]]; fi
pending=$(assert_pending ambiguous "$TEST_DIR/report-failure.err")
[[ $(<"$pending") == $'alpha\nbeta' ]]
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]
rm -rf "$(dirname "$pending")"

# The stable-empty pair and pre-paste recheck must finish before max-wait.
# Simulate a second capture that itself returns only after the deadline.
reset_case slow-second-empty
if run_sender target --submit-delay 0 --max-wait 1 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/slow-capture.err"; then
  echo 'FAIL: late empty observation was delivered after max-wait' >&2; exit 1
else rc=$?; [[ $rc -eq 2 ]]; fi
pending=$(assert_pending deferred "$TEST_DIR/slow-capture.err")
[[ $(grep -c '^paste-start$' "$FAKE_LOG" || true) -eq 0 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# The immediate pre-paste recheck is the third capture and must share max-wait.
reset_case slow-third-empty
if run_sender target --submit-delay 0 --max-wait 1 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/slow-third.err"; then
  echo 'FAIL: slow third empty observation was delivered after max-wait' >&2; exit 1
else rc=$?; [[ $rc -eq 2 ]]; fi
pending=$(assert_pending deferred "$TEST_DIR/slow-third.err")
[[ $(grep -c '^paste-start$' "$FAKE_LOG" || true) -eq 0 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# A signal while waiting before any paste must still retain the outgoing payload.
reset_case interrupt-before-paste
if FAKE_INTERRUPT_BEFORE_PASTE=1 run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/pre-paste-signal.err"; then
  echo 'FAIL: pre-paste interruption returned success' >&2; exit 1
else rc=$?; [[ $rc -eq 143 ]]; fi
pending=$(assert_pending deferred "$TEST_DIR/pre-paste-signal.err")
[[ $(<"$pending") == $'alpha\nbeta' ]]
[[ $(grep -c '^paste-start$' "$FAKE_LOG" || true) -eq 0 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# A clock subprocess failure after snapshot creation but before paste keeps a private deferred copy.
reset_case empty
if FAKE_FAIL_CLOCK_BEFORE_PASTE=1 run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/clock-failure.err"; then
  echo 'FAIL: injected pre-paste clock failure returned success' >&2; exit 1
else rc=$?; [[ $rc -eq 1 ]]; fi
pending=$(assert_pending deferred "$TEST_DIR/clock-failure.err")
[[ $(<"$pending") == $'alpha\nbeta' ]]
[[ $(grep -c '^paste-start$' "$FAKE_LOG" || true) -eq 0 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# Empty composer plus unrelated generation output still accepts queued input.
reset_case streaming
out=$(run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file")
[[ "$out" == 'status=sent receipt=unverified' ]]
[[ $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]

# A single stale empty frame after paste is tolerated until the paste renders exactly.
reset_case render-stale-empty
out=$(run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file")
[[ "$out" == 'status=sent receipt=unverified' ]]
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]

# Initial unknown repaint may settle, but a known mixed composer stops.
for mode in render-unknown render-late-unknown slow-first-own; do
  reset_case "$mode"
  out=$(run_sender target --render-wait 2 --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file")
  [[ "$out" == 'status=sent receipt=unverified' ]]
  [[ $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]
done
for mode in render-late-unknown render-unknown-human slow-first-own; do
  reset_case "$mode"
  if run_sender target --render-wait 1 --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/render-limit.err"; then
    echo 'FAIL: timeout or mixed render was submitted' >&2; exit 1
  else rc=$?; [[ $rc -eq 3 ]]; fi
  pending=$(assert_pending ambiguous "$TEST_DIR/render-limit.err")
  [[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
  rm -rf "$(dirname "$pending")"
done

# Persistently stale empty rendering times out ambiguously; it never presses Enter.
reset_case persistent-empty-after-paste
if run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/persistent-empty.err"; then
  echo 'FAIL: persistent stale empty rendering was submitted' >&2; exit 1
else rc=$?; [[ $rc -eq 3 ]]; fi
pending=$(assert_pending ambiguous "$TEST_DIR/persistent-empty.err")
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
[[ $(<"$FAKE_STATE/composer") == $'alpha\nbeta' ]]
rm -rf "$(dirname "$pending")"

# A persistent unknown render frame exhausts its budget without Enter.
reset_case unknown-after-paste
if run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/unknown-after-paste.err"; then
  echo 'FAIL: unknown post-paste frame was submitted' >&2; exit 1
else rc=$?; [[ $rc -eq 3 ]]; fi
pending=$(assert_pending ambiguous "$TEST_DIR/unknown-after-paste.err")
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# Existing human text, slash commands, and modal screens time out without paste/Enter.
for mode in human slash modal; do
  reset_case "$mode"
  if run_sender target --submit-delay 0 --max-wait 1 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/$mode.err"; then
    echo "FAIL: $mode composer was sent" >&2; exit 1
  else rc=$?; [[ $rc -eq 2 ]]; fi
  pending=$(assert_pending deferred "$TEST_DIR/$mode.err")
  [[ $(grep -c '^paste-start$' "$FAKE_LOG" || true) -eq 0 ]]
  [[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
  [[ $(<"$pending") == $'alpha\nbeta' ]]
  rm -rf "$(dirname "$pending")"
done

# A new human draft between stable-empty observations and paste defers safely.
reset_case human-before-paste
if run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/before-paste.err"; then
  echo 'FAIL: pre-paste human draft was sent' >&2; exit 1
else rc=$?; [[ $rc -eq 2 ]]; fi
pending=$(assert_pending deferred "$TEST_DIR/before-paste.err")
[[ $(grep -c '^paste-start$' "$FAKE_LOG" || true) -eq 0 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# If human input appears after our paste, do not Enter, erase, or paste again.
reset_case human-during-delay
if run_sender target --submit-delay 1 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/midflight.err"; then
  echo 'FAIL: changed composer was submitted' >&2; exit 1
else rc=$?; [[ $rc -eq 3 ]]; fi
pending=$(assert_pending ambiguous "$TEST_DIR/midflight.err")
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
[[ $(<"$FAKE_STATE/composer") == 'a human began typing' ]]
# A second attempt while the ambiguous composer is occupied must defer, never duplicate.
if run_sender target --submit-delay 0 --max-wait 1 --poll-interval 0.1 --prompt-file "$pending" 2>"$TEST_DIR/retry-occupied.err"; then
  echo 'FAIL: retry was pasted over occupied composer' >&2; exit 1
else rc=$?; [[ $rc -eq 2 ]]; fi
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
# A deliberate retry after the composer is empty delivers the retained payload exactly once.
rm -f "$FAKE_STATE/pasted"
: > "$FAKE_STATE/composer"
printf empty > "$FAKE_STATE/mode"
out=$(run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$pending")
[[ "$out" == 'status=sent receipt=unverified' ]]
[[ $(grep -c '^enter$' "$FAKE_LOG") -eq 1 ]]
rm -rf "$(dirname "$pending")"

# If the sender stops after paste, its pending message must not be pasted over
# the same exact visible payload on a deliberate retry.
reset_case empty
if FAKE_INTERRUPT_AFTER_PASTE=1 run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/interrupted.err"; then
  echo 'FAIL: injected post-paste interruption returned success' >&2; exit 1
else rc=$?; [[ $rc -ne 0 ]]; fi
pending=$(assert_pending ambiguous "$TEST_DIR/interrupted.err")
[[ $(<"$FAKE_STATE/composer") == $'alpha\nbeta' ]]
if run_sender target --submit-delay 0 --max-wait 1 --poll-interval 0.1 --prompt-file "$pending" 2>"$TEST_DIR/retry-own.err"; then
  echo 'FAIL: exact visible payload was submitted twice' >&2; exit 1
else rc=$?; [[ $rc -eq 2 ]]; fi
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# A parser subprocess failure after paste becomes unknown and keeps the payload.
reset_case empty
if FAKE_FAIL_PARSER_AFTER_PASTE=1 run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/parser-failure.err"; then
  echo 'FAIL: parser failure after paste was treated as success' >&2; exit 1
else rc=$?; [[ $rc -eq 3 ]]; fi
pending=$(assert_pending ambiguous "$TEST_DIR/parser-failure.err")
grep -q 'reason=post-paste-render-timeout' "$TEST_DIR/parser-failure.err"
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# Paste failure retains payload and never submits; the named buffer is cleaned.
reset_case empty
if FAKE_FAIL_PASTE=1 run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/paste.err"; then
  echo 'FAIL: paste failure reported success' >&2; exit 1
else rc=$?; [[ $rc -eq 3 ]]; fi
pending=$(assert_pending ambiguous "$TEST_DIR/paste.err")
[[ $(grep -c '^delete-buffer$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG" || true) -eq 0 ]]
rm -rf "$(dirname "$pending")"

# The default submit delay remains eight seconds, measured from paste to Enter.
reset_case empty
if out=$(FAKE_REAL_SLEEP=1 run_sender target --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file"); then
  [[ "$out" == 'status=sent receipt=unverified' ]]
else
  echo 'FAIL: successful prompt-file delivery returned nonzero' >&2
  exit 1
fi
python3 - "$FAKE_STATE/times" <<'PY'
from pathlib import Path
import sys
values = [int(x) for x in Path(sys.argv[1]).read_text().splitlines()]
assert len(values) == 2, values
elapsed = (values[1] - values[0]) / 1_000_000_000
assert 7.8 <= elapsed <= 11.5, elapsed
PY

# A target-lock timeout is a deferred, privately retained delivery.
reset_case empty
if FAKE_FAIL_FLOCK=1 run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" 2>"$TEST_DIR/lock.err"; then
  echo 'FAIL: failed target lock was ignored' >&2; exit 1
else rc=$?; [[ $rc -eq 2 ]]; fi
pending=$(assert_pending deferred "$TEST_DIR/lock.err")
grep -q 'reason=target-lock-timeout' "$TEST_DIR/lock.err"
[[ ! -s "$FAKE_LOG" ]]
rm -rf "$(dirname "$pending")"

# Aliases resolving to the same pane serialize the full paste/delay/submit sequence.
reset_case empty
run_sender alias-one --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" & one=$!
run_sender_without_xdg_runtime alias-two --submit-delay 0 --max-wait 3 --poll-interval 0.1 --prompt-file "$prompt_file" & two=$!
wait "$one"; wait "$two"
python3 - "$FAKE_LOG" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text().splitlines()
assert lines == ['paste-start','paste-end','enter','paste-start','paste-end','enter'], lines
PY

# New transaction mode must recover an intentionally unconsumed first Enter
# using exact current ownership, with one paste total and two key attempts.
reset_case empty
FAKE_IGNORE_FIRST_ENTER=1 run_sender target --submit-delay 0 --max-wait 3 --poll-interval 0.1 \
  --attempt-file "$TEST_DIR/synthetic-attempt" --prompt-file "$prompt_file" > /dev/null
[[ -s "$FAKE_STATE/composer" ]]
run_sender target --submit-delay 0 --submit-pending --poll-interval 0.1 \
  --attempt-file "$TEST_DIR/synthetic-attempt" --prompt-file "$prompt_file" > /dev/null
[[ $(grep -c '^paste-start$' "$FAKE_LOG") -eq 1 ]]
[[ $(grep -c '^enter$' "$FAKE_LOG") -eq 2 ]]
cmp "$FAKE_STATE/submitted" "$prompt_file"

# A delayed application receipt observed during the guarded continuation
# suppresses Enter, even when its composer still paints our exact old bytes.
reset_case empty
cp "$prompt_file" "$FAKE_STATE/composer"
out=$(FAKE_RECEIPT_AFTER_GUARDS=3 run_sender target --submit-delay 0 --submit-pending \
  --attempt-file "$TEST_DIR/synthetic-attempt" --prompt-file "$prompt_file")
[[ "$out" == 'status=received receipt=received' ]]
[[ ! -s "$FAKE_LOG" ]]
cmp "$FAKE_STATE/composer" "$prompt_file"

# Human mixing, modals, wrong bytes and unreadable attempt evidence never
# authorize continuation. No render grace is permitted for retained input.
for mode in human-during-delay modal wrong-bytes unknown; do
  reset_case "$mode"
  cp "$prompt_file" "$FAKE_STATE/composer"
  printf 'pasted\n' > "$FAKE_STATE/pasted"
  if [[ "$mode" == wrong-bytes ]]; then printf 'different human draft' > "$FAKE_STATE/composer"; fi
  if [[ "$mode" == unknown ]]; then unknown=1; else unknown=0; fi
  if FAKE_ATTEMPT_UNKNOWN=$unknown run_sender target --submit-delay 1 --submit-pending --poll-interval 0.1 \
      --attempt-file "$TEST_DIR/synthetic-attempt" --prompt-file "$prompt_file" 2>"$TEST_DIR/continuation.err"; then
    echo "FAIL: unsafe $mode continuation was accepted" >&2; exit 1
  else rc=$?; [[ $rc -eq 3 ]]; fi
  [[ ! -s "$FAKE_LOG" ]]
  pending=$(assert_pending ambiguous "$TEST_DIR/continuation.err")
  cmp "$pending" "$prompt_file"
  rm -rf "$(dirname "$pending")"
done
reset_case empty
if run_sender target --submit-pending --prompt-file "$prompt_file" 2>"$TEST_DIR/no-attempt.err"; then
  echo 'FAIL: continuation without receipt baseline was accepted' >&2; exit 1
fi
[[ ! -s "$FAKE_STATE/tmux-calls" ]]

echo 'claude-send composer protection and continuation tests passed'
