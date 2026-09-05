#!/bin/bash
# Hermetic tests for interactive spawning, compaction, and JSONL recap watching.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPAWN="$SCRIPT_DIR/agent-spawn.sh"
RESOLVE="$SCRIPT_DIR/codex-rollout-path.sh"
WATCH="$SCRIPT_DIR/codex-jsonl-watch.py"
TEST_DIR=$(mktemp -d /tmp/test-agent-spawn.XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR/bin" "$TEST_DIR/repo with spaces"
PROMPT="$TEST_DIR/prompt"
COMPACT_PROMPT="$TEST_DIR/repo with spaces/compact prompt.txt"
LOG="$TEST_DIR/tmux.log"
LAUNCH="$TEST_DIR/launch"
STDERR="$TEST_DIR/stderr"
LAST_BUFFER="$TEST_DIR/last-buffer"
ROLLOUT="$TEST_DIR/.codex/sessions/2026/09/05/rollout-2026-09-05T03-00-00-01a07050-1111-7222-8333-444444444444.jsonl"
PARENT_ROLLOUT="$TEST_DIR/.codex/sessions/2026/09/05/rollout-2026-09-05T02-00-00-01a0702a-7b5e-71e0-bb62-287d55a7f9ba.jsonl"
PROC_ROOT="$TEST_DIR/proc"

printf '%s\n' 'Design the step, then recap it.' > "$PROMPT"
printf '%s\n' 'Keep decisions and contracts; discard repetitive terminal output.' > "$COMPACT_PROMPT"
mkdir -p "$(dirname "$ROLLOUT")" \
    "$PROC_ROOT/4242/task/4242" "$PROC_ROOT/4242/fd" \
    "$PROC_ROOT/4243/task/4243" "$PROC_ROOT/4243/fd" \
    "$PROC_ROOT/4244/task/4244" "$PROC_ROOT/4244/fd"
printf '%s\n' '{"type":"session_meta","payload":{"id":"01a07050-1111-7222-8333-444444444444"}}' > "$ROLLOUT"
printf '%s\n' '{"type":"session_meta","payload":{"id":"01a0702a-7b5e-71e0-bb62-287d55a7f9ba"}}' > "$PARENT_ROLLOUT"
printf '%s\n' '4243' > "$PROC_ROOT/4242/task/4242/children"
printf '%s\n' '4244' > "$PROC_ROOT/4243/task/4243/children"
: > "$PROC_ROOT/4244/task/4244/children"
ln -s "$PARENT_ROLLOUT" "$PROC_ROOT/4243/fd/6"
ln -s "$ROLLOUT" "$PROC_ROOT/4244/fd/7"

cat > "$TEST_DIR/bin/tmux" <<'STUB'
#!/bin/bash
set -eu
printf '%q ' "$@" >> "$FAKE_TMUX_LOG"
printf '\n' >> "$FAKE_TMUX_LOG"
case "${1:-}" in
    new-window)
        printf '%s\n' "${!#}" > "$FAKE_LAUNCH_FILE"
        ;;
    display-message)
        case " $* " in
            *' -t test:department '*|*' -t test:relative '*) ;;
            *) exit 64 ;;
        esac
        printf '%s\n' '4242'
        ;;
    load-buffer)
        printf '%s' "$(<"${!#}")" > "$FAKE_LAST_BUFFER"
        ;;
    send-keys)
        if [[ "$(<"$FAKE_LAST_BUFFER")" == '/compact' && \
              "${FAKE_COMPACTION_MODE:-complete}" == complete ]]; then
            printf '%s\n' '{"type":"event_msg","payload":{"type":"context_compacted"}}' >> "$FAKE_ROLLOUT"
        fi
        ;;
    has-session|new-session|paste-buffer|delete-buffer)
        ;;
    capture-pane)
        composer='Ask Codex to do anything'
        if [[ -f "$FAKE_LAST_BUFFER" && "$(<"$FAKE_LAST_BUFFER")" == '/compact' ]]; then
            composer='/compact'
        fi
        case "${FAKE_PANE_MODE:-codex-ready}" in
            codex-ready)
                printf '%s\n' '╭ OpenAI Codex ╮' "› $composer" 'gpt-5.6-sol · Context 82% left'
                ;;
            codex-ready-with-old-dialog)
                printf '%s\n' '1. Update now' 'Press enter to continue' "› $composer" 'gpt-5.6-sol · Context 82% left'
                ;;
            update-over-old-composer)
                printf '%s\n' '› Ask Codex to do anything' 'gpt-5.6-sol · Context 82% left' '✨ Update available!' '› 1. Update now' 'Press enter to continue'
                ;;
            update)
                printf '%s\n' '✨ Update available!' '› 1. Update now' '  2. Skip' 'Press enter to continue'
                ;;
            trust)
                printf '%s\n' 'Do you trust the contents of this project?' '> Yes, I trust this folder' 'enter Confirm'
                ;;
            agy-ready)
                printf '%s\n' '────────────────' 'Type your message' '? for shortcuts'
                ;;
            agy-trust-over-composer)
                printf '%s\n' '────────────────' '? for shortcuts' 'Do you trust the contents of this project?' '> Yes, I trust this folder' 'enter Confirm'
                ;;
            vanished)
                exit 1
                ;;
        esac
        ;;
esac
STUB

cat > "$TEST_DIR/bin/sleep" <<'STUB'
#!/bin/bash
exit 0
STUB
cat > "$TEST_DIR/bin/codex" <<'STUB'
#!/bin/bash
printf '%s\n' "$@" > "$FAKE_CODEX_ARGS"
STUB
chmod +x "$TEST_DIR/bin/tmux" "$TEST_DIR/bin/sleep" "$TEST_DIR/bin/codex"

run_spawn() {
    local mode="$1"
    shift
    env -u TMUX \
        PATH="$TEST_DIR/bin:$PATH" \
        FAKE_TMUX_LOG="$LOG" \
        FAKE_LAUNCH_FILE="$LAUNCH" \
        FAKE_LAST_BUFFER="$LAST_BUFFER" \
        FAKE_PANE_MODE="$mode" \
        FAKE_ROLLOUT="$ROLLOUT" \
        FAKE_COMPACTION_MODE="${FAKE_COMPACTION_MODE:-complete}" \
        SPAWN_PROC_ROOT="$PROC_ROOT" \
        "$SPAWN" "test:department" codex "$TEST_DIR/repo with spaces" \
        --fork-session 01a0702a-7b5e-71e0-bb62-287d55a7f9ba \
        --prompt-file "$PROMPT" "$@"
}

run_agy() {
    env -u TMUX \
        PATH="$TEST_DIR/bin:$PATH" \
        FAKE_TMUX_LOG="$LOG" \
        FAKE_LAUNCH_FILE="$LAUNCH" \
        FAKE_LAST_BUFFER="$LAST_BUFFER" \
        FAKE_PANE_MODE="$1" \
        FAKE_ROLLOUT="$ROLLOUT" \
        "$SPAWN" test:department agy "$TEST_DIR/repo with spaces" \
        --prompt-file "$PROMPT"
}

assert_no_input() {
    if grep -Eq '^(load-buffer|paste-buffer|send-keys)' "$LOG"; then
        echo "FAIL: unsafe input reached tmux" >&2
        cat "$LOG" >&2
        exit 1
    fi
}

# A fork is an explicit, shell-escaped `codex fork`, and its prompt is pasted
# only after the live composer is observed.
: > "$LOG"
run_spawn codex-ready >/dev/null
grep -Fq 'codex fork ' "$LAUNCH"
grep -Fq -- '-C ' "$LAUNCH"
grep -Fq 'check_for_update_on_startup=false' "$LAUNCH"
grep -Fq '01a0702a-7b5e-71e0-bb62-287d55a7f9ba' "$LAUNCH"
grep -Fq 'repo\ with\ spaces' "$LAUNCH"
grep -q '^load-buffer ' "$LOG"
grep -q '^paste-buffer ' "$LOG"
grep -q '^send-keys ' "$LOG"

# Dialog text in restored history does not mask a valid composer.
: > "$LOG"
run_spawn codex-ready-with-old-dialog >/dev/null
grep -q '^paste-buffer ' "$LOG"

# The update suppression is scoped to every Codex spawned here, not only
# forks. The modal detector remains covered below as a backstop.
: > "$LOG"
env -u TMUX PATH="$TEST_DIR/bin:$PATH" FAKE_TMUX_LOG="$LOG" \
    FAKE_LAUNCH_FILE="$LAUNCH" FAKE_LAST_BUFFER="$LAST_BUFFER" \
    FAKE_PANE_MODE=codex-ready FAKE_ROLLOUT="$ROLLOUT" \
    "$SPAWN" test:ordinary codex "$TEST_DIR/repo with spaces" \
    --prompt-file "$PROMPT" >/dev/null
grep -Fq 'check_for_update_on_startup=false' "$LAUNCH"

# A live update chooser and trust prompt fail closed: no paste, no Enter.
for mode in update update-over-old-composer trust; do
    : > "$LOG"
    if run_spawn "$mode" >/dev/null 2>"$STDERR"; then
        echo "FAIL: $mode modal was treated as a composer" >&2
        exit 1
    fi
    grep -Fq 'Nothing was pasted, and no key was sent' "$STDERR"
    assert_no_input
done

# agy trust is checked before its live-frame composer hints, because the modal
# can be painted over an already-rendered frame.
: > "$LOG"
run_agy agy-ready >/dev/null
grep -q '^paste-buffer ' "$LOG"
: > "$LOG"
if run_agy agy-trust-over-composer >/dev/null 2>"$STDERR"; then
    echo 'FAIL: agy trust modal was treated as a composer' >&2
    exit 1
fi
grep -Fq 'permission decision' "$STDERR"
assert_no_input

# A disappearing process fails without input, and --fork-session cannot be
# silently applied to a different provider.
: > "$LOG"
if run_spawn vanished >/dev/null 2>"$STDERR"; then
    echo 'FAIL: vanished window reported success' >&2
    exit 1
fi
grep -Fq 'exited before the prompt could be sent' "$STDERR"
assert_no_input

: > "$LOG"
if env -u TMUX PATH="$TEST_DIR/bin:$PATH" FAKE_TMUX_LOG="$LOG" FAKE_LAUNCH_FILE="$LAUNCH" \
    "$SPAWN" test:department claude . \
    --fork-session 01a0702a-7b5e-71e0-bb62-287d55a7f9ba \
    --prompt-file "$PROMPT" >/dev/null 2>"$STDERR"; then
    echo 'FAIL: non-Codex fork reported success' >&2
    exit 1
fi
grep -Fq 'supported only for codex' "$STDERR"
[[ ! -s "$LOG" ]]

# The rollout resolver follows the pane's process tree instead of guessing
# which simultaneously-created fork file is newest.
resolved=$(env -u TMUX PATH="$TEST_DIR/bin:$PATH" FAKE_TMUX_LOG="$LOG" \
    FAKE_LAUNCH_FILE="$LAUNCH" FAKE_LAST_BUFFER="$LAST_BUFFER" \
    SPAWN_PROC_ROOT="$PROC_ROOT" "$RESOLVE" --exclude-session \
    01a0702a-7b5e-71e0-bb62-287d55a7f9ba test:department)
[[ "$resolved" == "$ROLLOUT" ]]

# Relative directories are canonicalized once, then reused for both tmux -c
# and Codex -C instead of being resolved a second time inside the new pane.
: > "$LOG"
(
    cd "$TEST_DIR"
    env -u TMUX PATH="$TEST_DIR/bin:$PATH" FAKE_TMUX_LOG="$LOG" \
        FAKE_LAUNCH_FILE="$LAUNCH" FAKE_LAST_BUFFER="$LAST_BUFFER" \
        FAKE_PANE_MODE=codex-ready FAKE_ROLLOUT="$ROLLOUT" \
        "$SPAWN" test:relative codex 'repo with spaces' \
        --fork-session 01a0702a-7b5e-71e0-bb62-287d55a7f9ba \
        --prompt-file "$PROMPT" >/dev/null
)
grep -Fq "$TEST_DIR/repo\\ with\\ spaces" "$LAUNCH"
grep -Fq "$TEST_DIR/repo\\ with\\ spaces" "$LOG"

# Optional launch compaction passes only a prompt path to Codex, submits bare
# /compact, observes a structured completion event, and only then loads the
# task. It also records the exact child rollout atomically for the parent.
: > "$LOG"
ROLLOUT_OUT="$TEST_DIR/child-rollout"
CURSOR_OUT="$TEST_DIR/recap-cursor"
run_spawn codex-ready \
    --compact-prompt-file "$COMPACT_PROMPT" \
    --rollout-path-file "$ROLLOUT_OUT" \
    --recap-cursor-file "$CURSOR_OUT" >/dev/null
grep -Fq 'experimental_compact_prompt_file' "$LAUNCH"
grep -Fq 'check_for_update_on_startup=false' "$LAUNCH"
if grep -Fq 'Keep decisions and contracts' "$LAUNCH"; then
    echo 'FAIL: compaction prompt body leaked into argv' >&2
    exit 1
fi
FAKE_CODEX_ARGS="$TEST_DIR/codex.args" PATH="$TEST_DIR/bin:$PATH" \
    bash -c "$(<"$LAUNCH")"
grep -Fxq 'experimental_compact_prompt_file="'"$COMPACT_PROMPT"'"' "$TEST_DIR/codex.args"
mapfile -t codex_args < "$TEST_DIR/codex.args"
for index in "${!codex_args[@]}"; do
    if [[ "${codex_args[index]}" == experimental_compact_prompt_file=* ]]; then
        (( index > 0 )) && [[ "${codex_args[index - 1]}" == '-c' ]]
    fi
done
[[ "$(<"$ROLLOUT_OUT")" == "$ROLLOUT" ]]
[[ "$(<"$CURSOR_OUT")" =~ ^[0-9]+$ ]]
mapfile -t loaded_buffers < <(awk '$1 == "load-buffer" { print $3 }' "$LOG")
[[ ${#loaded_buffers[@]} -eq 2 ]]
[[ "${loaded_buffers[0]}" == spawn-agent-compact-* ]]
[[ "${loaded_buffers[1]}" == spawn-agent-task-* ]]
grep -Fq "$PROMPT" "$LOG"

# If structured compaction completion never arrives, fail with the task still
# unsent. The only loaded input is the explicitly-authorized /compact command.
: > "$LOG"
printf '%s\n' '{"type":"session_meta","payload":{"id":"01a07050-1111-7222-8333-444444444444"}}' > "$ROLLOUT"
if FAKE_COMPACTION_MODE=none run_spawn codex-ready \
    --compact-prompt-file "$COMPACT_PROMPT" --compact-timeout 1 \
    >/dev/null 2>"$STDERR"; then
    echo 'FAIL: missing compaction completion reported success' >&2
    exit 1
fi
grep -Fq 'task prompt not sent' "$STDERR"
[[ $(grep -c '^load-buffer ' "$LOG") -eq 1 ]]
if grep -Fq "$PROMPT" "$LOG"; then
    echo 'FAIL: task was loaded before compaction completed' >&2
    exit 1
fi

# The JSONL watcher ignores prompt/user/commentary/non-prefix matches. A valid
# final assistant record is invisible until its terminating newline arrives,
# then its full multi-part output is printed exactly once.
JSONL="$TEST_DIR/watcher.jsonl"
WATCH_OUT="$TEST_DIR/watcher.out"
MARKER='STEP-JSONL-91ac COMPLETE — EXECUTIVE RECAP'
EXPECTED="$MARKER
All work is verified."
: > "$JSONL"
"$WATCH" recap "$JSONL" "$MARKER" 5 > "$WATCH_OUT" &
watch_pid=$!
printf '%s\n' \
    '{"type":"response_item","payload":{"type":"message","role":"user","phase":"final_answer","content":[{"type":"output_text","text":"STEP-JSONL-91ac COMPLETE — EXECUTIVE RECAP"}]}}' \
    '{"type":"response_item","payload":{"type":"message","role":"assistant","phase":"commentary","content":[{"type":"output_text","text":"STEP-JSONL-91ac COMPLETE — EXECUTIVE RECAP"}]}}' \
    '{"type":"response_item","payload":{"type":"message","role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"prefix STEP-JSONL-91ac COMPLETE — EXECUTIVE RECAP"}]}}' \
    >> "$JSONL"
matching='{"type":"response_item","payload":{"type":"message","role":"assistant","phase":"final_answer","content":[{"type":"output_text","text":"STEP-JSONL-91ac COMPLETE — EXECUTIVE RECAP\n"},{"type":"output_text","text":"All work is verified."}]}}'
split=$(( ${#matching} / 2 ))
printf '%s' "${matching:0:split}" >> "$JSONL"
/bin/sleep 0.4
kill -0 "$watch_pid"
[[ ! -s "$WATCH_OUT" ]]
printf '%s\n' "${matching:split}" >> "$JSONL"
wait "$watch_pid"
[[ "$(<"$WATCH_OUT")" == "$EXPECTED" ]]
[[ $(grep -oF "$MARKER" "$WATCH_OUT" | wc -l) -eq 1 ]]

# Session IDs resolve to rollout paths, and cursor stops after the final
# newline rather than skipping an incomplete trailing record.
session_result=$(CODEX_HOME="$TEST_DIR/.codex" "$WATCH" recap \
    01A07050-1111-7222-8333-444444444444 "$MARKER" 1 \
    --after-byte 0 2>"$STDERR" || true)
[[ -z "$session_result" ]]
grep -Fq 'timed out' "$STDERR"
printf '{}\n{"partial"' > "$JSONL"
[[ "$($WATCH cursor "$JSONL")" -eq 3 ]]

if "$WATCH" recap "$JSONL" "$MARKER" nan >/dev/null 2>"$STDERR"; then
    echo 'FAIL: non-finite timeout was accepted' >&2
    exit 1
fi
grep -Fq 'timeout must be a positive number' "$STDERR"

echo 'PASS: Codex fork spawning, launch compaction, and JSONL recap watching'
