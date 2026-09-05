#!/bin/bash
# agent-spawn.sh - Spawn an AI agent in a tmux window (interactive mode)
#
# Usage: agent-spawn.sh <session:window> <agent> [directory] [prompt]
#        agent-spawn.sh <session:window> <agent> [directory] --prompt-file <file>
#
# Agents: claude, codex, agy (Antigravity CLI)
#
# Options:
#   -f, --prompt-file <file>          Read prompt from file (avoids ARG_MAX)
#   -F, --fork-session <uuid>         Fork this Codex session interactively
#       --compact-prompt-file <file>  Compact inherited history before the task
#       --compact-timeout <seconds>   Compaction timeout (default: 600)
#       --rollout-path-file <file>    Record the child Codex rollout path
#       --recap-cursor-file <file>    Record where recap watching should begin
#
# Automatically targets the correct tmux server socket when run inside tmux.
# Override with SPAWN_TMUX_SOCKET or SPAWN_TMUX_LABEL env vars.
#
# The prompt is delivered via tmux load-buffer/paste-buffer after the agent
# starts, so it never appears in any execve(2) argument list.
#
# Examples:
#   agent-spawn.sh chaos:review claude ./project "Review auth module"
#   agent-spawn.sh chaos:codex-help codex . "Help me refactor this"
#   agent-spawn.sh chaos:review claude ./project --prompt-file /tmp/prompt.txt
#   agent-spawn.sh chaos:design codex ./project --fork-session UUID --prompt-file /tmp/prompt.txt

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/tmux-ctx.sh"

codex_composer_ready() {
    local active_tail last_nonempty
    active_tail=$(printf '%s\n' "$1" | tail -n 6)
    last_nonempty=$(printf '%s\n' "$active_tail" | awk 'NF { line = $0 } END { print line }')
    [[ "$last_nonempty" =~ Context[[:space:]][0-9]+% ]] && \
        printf '%s\n' "$active_tail" | grep -q '^[[:space:]]*› '
}

usage() {
    echo "Usage: agent-spawn.sh <session:window> <agent> [directory] [prompt]" >&2
    echo "       agent-spawn.sh <session:window> <agent> [directory] --prompt-file <file>" >&2
    echo "  session:window: tmux target (e.g., 'chaos:review')" >&2
    echo "  agent: claude, codex, agy" >&2
    echo "  directory: working directory (default: current)" >&2
    echo "  prompt: initial prompt for the agent" >&2
    echo "  --prompt-file: read prompt from file (avoids ARG_MAX)" >&2
    echo "  --fork-session: interactively fork a Codex session UUID" >&2
    echo "  --compact-prompt-file: compact inherited history with this prompt before the task" >&2
    echo "  --compact-timeout: seconds to wait for structured compaction completion" >&2
    echo "  --rollout-path-file: atomically write the child Codex rollout path" >&2
    echo "  --recap-cursor-file: atomically write the safe JSONL recap start offset" >&2
    exit 1
}

if [[ $# -lt 2 ]]; then
    usage
fi

target="$1"
agent="$2"
shift 2

# Parse remaining args: [directory] [prompt] or --prompt-file <file>
# --prompt-file can appear anywhere in the remaining args.
prompt_file=""
prompt=""
directory="."
fork_session=""
compact_prompt_file=""
compact_timeout=600
compact_timeout_set=""
rollout_path_file=""
recap_cursor_file=""
_cleanup_prompt_file=""
_cleanup_command_file=""
_cleanup_rollout_output=""
_cleanup_cursor_output=""

# Trap to clean up temp files on exit/interrupt (only files we created).
# Set early so failure paths during parsing don't leak temp files.
# An EXIT trap's status BECOMES the script's, so cleanup must be unable to
# fail. Without `return 0` every no-prompt spawn -- where the guard is simply
# false -- reported failure after succeeding; without `|| true` a failing rm
# would kill the trap under `set -e` before it got there.
cleanup() {
    if [[ -n "$_cleanup_prompt_file" ]]; then rm -f "$_cleanup_prompt_file" || true; fi
    if [[ -n "$_cleanup_command_file" ]]; then rm -f "$_cleanup_command_file" || true; fi
    if [[ -n "$_cleanup_rollout_output" ]]; then rm -f "$_cleanup_rollout_output" || true; fi
    if [[ -n "$_cleanup_cursor_output" ]]; then rm -f "$_cleanup_cursor_output" || true; fi
    return 0
}
trap cleanup EXIT

positional=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --prompt-file|-f)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --prompt-file requires a file path" >&2
                exit 1
            fi
            prompt_file="$2"
            if [[ ! -f "$prompt_file" ]]; then
                echo "Error: prompt file not found: $prompt_file" >&2
                exit 1
            fi
            if [[ ! -s "$prompt_file" ]]; then
                echo "Error: prompt file is empty: $prompt_file" >&2
                exit 1
            fi
            shift 2
            ;;
        --fork-session|-F)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --fork-session requires a Codex session UUID" >&2
                exit 1
            fi
            fork_session="$2"
            shift 2
            ;;
        --compact-prompt-file)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --compact-prompt-file requires a file path" >&2
                exit 1
            fi
            compact_prompt_file="$2"
            if [[ ! -f "$compact_prompt_file" ]]; then
                echo "Error: compaction prompt file not found: $compact_prompt_file" >&2
                exit 1
            fi
            shift 2
            ;;
        --compact-timeout)
            if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+$ || "$2" -eq 0 ]]; then
                echo "Error: --compact-timeout requires a positive integer" >&2
                exit 1
            fi
            compact_timeout="$2"
            compact_timeout_set=1
            shift 2
            ;;
        --rollout-path-file)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --rollout-path-file requires a file path" >&2
                exit 1
            fi
            rollout_path_file="$2"
            shift 2
            ;;
        --recap-cursor-file)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --recap-cursor-file requires a file path" >&2
                exit 1
            fi
            recap_cursor_file="$2"
            shift 2
            ;;
        *)
            positional+=("$1")
            shift
            ;;
    esac
done

# Positional args: [directory] [prompt]
if [[ ${#positional[@]} -ge 1 ]]; then
    directory="${positional[0]}"
fi
if [[ ${#positional[@]} -ge 2 ]]; then
    prompt="${positional[1]}"
fi

if ! directory=$(cd -- "$directory" 2>/dev/null && pwd); then
    echo "Error: working directory does not exist or is not accessible: $directory" >&2
    exit 1
fi

# Parse session and window from target
session="${target%%:*}"
window="${target#*:}"

# Validate agent
case "$agent" in
    claude|codex|agy) ;;
    *)
        echo "Unknown agent: $agent" >&2
        echo "Supported agents: claude, codex, agy" >&2
        exit 1
        ;;
esac

if [[ -n "$fork_session" ]]; then
    if [[ "$agent" != "codex" ]]; then
        echo "Error: --fork-session is currently supported only for codex" >&2
        exit 1
    fi
    if [[ ! "$fork_session" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]]; then
        echo "Error: --fork-session requires a UUID, got: $fork_session" >&2
        exit 1
    fi
fi

if [[ -n "$compact_prompt_file" ]]; then
    if [[ "$agent" != "codex" || -z "$fork_session" ]]; then
        echo "Error: --compact-prompt-file requires a Codex --fork-session launch" >&2
        exit 1
    fi
    if [[ ! -s "$compact_prompt_file" ]]; then
        echo "Error: compaction prompt file is empty: $compact_prompt_file" >&2
        exit 1
    fi
    if [[ -z "$prompt_file" && -z "$prompt" ]]; then
        echo "Error: launch-time compaction requires a task prompt to deliver afterward" >&2
        exit 1
    fi
fi

if [[ -n "$rollout_path_file" && ( "$agent" != "codex" || -z "$fork_session" ) ]]; then
    echo "Error: --rollout-path-file requires a Codex --fork-session launch" >&2
    exit 1
fi
if [[ -n "$rollout_path_file" && -z "$prompt_file" && -z "$prompt" ]]; then
    echo "Error: --rollout-path-file requires a task prompt in the fork workflow" >&2
    exit 1
fi
if [[ -n "$recap_cursor_file" && -z "$rollout_path_file" ]]; then
    echo "Error: --recap-cursor-file requires --rollout-path-file" >&2
    exit 1
fi
if [[ -n "$compact_timeout_set" && -z "$compact_prompt_file" ]]; then
    echo "Error: --compact-timeout requires --compact-prompt-file" >&2
    exit 1
fi

# Ensure session exists
if ! "${TMUX_CMD[@]}" has-session -t "$session" 2>/dev/null; then
    echo "Creating tmux session: $session" >&2
    "${TMUX_CMD[@]}" new-session -d -s "$session"
fi

# Normalize prompt to a file if provided as positional arg
if [[ -n "$prompt" && -z "$prompt_file" ]]; then
    prompt_file=$(mktemp /tmp/spawn-agent-prompt.XXXXXX)
    _cleanup_prompt_file="$prompt_file"
    printf '%s' "$prompt" > "$prompt_file"
fi

# Canonicalize prompt_file to absolute path
if [[ -n "$prompt_file" ]]; then
    prompt_file="$(cd -- "$(dirname -- "$prompt_file")" && pwd)/$(basename -- "$prompt_file")"
fi

if [[ -n "$compact_prompt_file" ]]; then
    compact_prompt_file="$(cd -- "$(dirname -- "$compact_prompt_file")" && pwd)/$(basename -- "$compact_prompt_file")"
    if [[ "$compact_prompt_file" =~ [[:cntrl:]] ]]; then
        echo "Error: compaction prompt path cannot contain control characters" >&2
        exit 1
    fi
fi

if [[ -n "$rollout_path_file" ]]; then
    rollout_dir=$(cd -- "$(dirname -- "$rollout_path_file")" && pwd) || {
        echo "Error: rollout path directory does not exist: $(dirname -- "$rollout_path_file")" >&2
        exit 1
    }
    rollout_path_file="$rollout_dir/$(basename -- "$rollout_path_file")"
fi

if [[ -n "$recap_cursor_file" ]]; then
    cursor_dir=$(cd -- "$(dirname -- "$recap_cursor_file")" && pwd) || {
        echo "Error: recap cursor directory does not exist: $(dirname -- "$recap_cursor_file")" >&2
        exit 1
    }
    recap_cursor_file="$cursor_dir/$(basename -- "$recap_cursor_file")"
fi

# Build one shell-escaped command string for tmux. A bare `codex fork` is the
# interactive transcript-preserving command; `codex exec fork` is headless and
# therefore intentionally does not belong here. Explicit -C avoids Codex's
# current-vs-saved-directory chooser, which is another modal where an automated
# Enter would be unsafe.
agent_command=("$agent")
if [[ "$agent" == "codex" ]]; then
    # Managed agent windows must not stop at an update chooser. Keep this a
    # process-local override: ordinary Codex launches still perform their
    # normal update check, and no global configuration is changed.
    agent_command=(codex -c check_for_update_on_startup=false)
fi
if [[ -n "$fork_session" ]]; then
    agent_command=(codex fork -c check_for_update_on_startup=false)
    if [[ -n "$compact_prompt_file" ]]; then
        # Pass only the path in argv, never the prompt body. TOML basic-string
        # escaping handles spaces, quotes, and backslashes without touching
        # the user's global Codex configuration.
        toml_compact_path=${compact_prompt_file//\\/\\\\}
        toml_compact_path=${toml_compact_path//\"/\\\"}
        agent_command+=(-c "experimental_compact_prompt_file=\"$toml_compact_path\"")
    fi
    agent_command+=(-C "$directory" "$fork_session")
fi
printf -v agent_shell_command '%q ' "${agent_command[@]}"

# Start the agent in a new tmux window, then deliver the prompt via
# load-buffer/paste-buffer. This avoids ARG_MAX at every boundary:
# the tmux command, the shell inside tmux, and the agent's execve.
"${TMUX_CMD[@]}" new-window -t "$session" -n "$window" -c "$directory" "$agent_shell_command"

if [[ -n "$prompt_file" ]]; then
    # Wait for the actual composer before sending anything. In particular,
    # Codex may show an update chooser first; the old fixed sleep pasted the
    # task into that chooser and its Enter selected "Update now".
    if [[ "$agent" == "agy" || "$agent" == "codex" ]]; then
        # agy paints a full-screen TUI and discards anything delivered before it
        # is listening, so a flat `sleep 2` races it -- on a cold start the
        # prompt vanishes and the spawn still reports success.
        #
        # Worse, agy's FIRST run in an untrusted directory opens a modal asking
        # whether to trust it, whose default button is "Yes, I trust this
        # folder" and whose footer is "enter Confirm". Pasting into that does
        # nothing and the Enter that follows GRANTS TRUST. That is how
        # /tmp/pr64-test ended up in agy's trustedWorkspaces during testing.
        # Never send keys until the composer specifically is on screen.
        agent_ready=""
        agent_gone=0
        for _ in $(seq 1 120); do
            # A window that disappeared is not a slow window: say so, rather
            # than burning 30s and then claiming the agent is waiting for input.
            if pane="$("${TMUX_CMD[@]}" capture-pane -p -t "$session:$window" 2>/dev/null)"; then
                agent_gone=0
            else
                agent_gone=$(( agent_gone + 1 ))
                if (( agent_gone >= 3 )); then
                    echo "Error: $session:$window is gone -- $agent exited before the prompt could be sent." >&2
                    echo "  Nothing was pasted." >&2
                    exit 1
                fi
                sleep 0.25
                continue
            fi

            # Only inspect the active bottom of the pane. A Codex fork can
            # contain old transcript text that quotes one of these dialogs.
            pane_tail=$(printf '%s\n' "$pane" | tail -n 30)

            if [[ "$agent" == "codex" ]]; then
                # A live composer's Context status is the bottommost non-empty
                # line. This distinguishes it from a stale composer in restored
                # history when a modal is actually on top.
                if codex_composer_ready "$pane_tail"; then
                    agent_ready=1
                    break
                fi
                if [[ "$pane_tail" == *"Update available!"* && "$pane_tail" == *"Press enter to continue"* ]]; then
                    echo "Error: Codex is showing its update chooser in $session:$window." >&2
                    echo "  Nothing was pasted, and no key was sent -- selecting an update is an" >&2
                    echo "  operator decision. Resolve the chooser in that window, then send the" >&2
                    echo "  prompt explicitly or start the fork again." >&2
                    exit 1
                fi
                if [[ "$pane_tail" == *"Do you trust the contents of this project?"* ]]; then
                    echo "Error: Codex is asking whether to trust this directory before it will run:" >&2
                    echo "    $directory" >&2
                    echo "  Nothing was pasted, and no key was sent -- answering that prompt is a" >&2
                    echo "  permission decision this tooling will not make for you." >&2
                    echo "  Answer it yourself, then send the prompt explicitly." >&2
                    exit 1
                fi
            elif [[ "$pane_tail" == *"Do you trust the contents of this project?"* ]]; then
                echo "Error: agy is asking whether to trust this directory before it will run:" >&2
                echo "    $directory" >&2
                echo "  Nothing was pasted, and no key was sent -- answering that prompt is a" >&2
                echo "  permission decision this tooling will not make for you." >&2
                echo "  Answer it yourself, then send the prompt explicitly." >&2
                exit 1
            # agy's composer specifically: its rule AND shortcuts hint. Check
            # trust first because a modal may be painted over a live frame.
            elif [[ "$pane_tail" == *"? for shortcuts"* && "$pane_tail" == *"─"* ]]; then
                agent_ready=1
                break
            fi
            sleep 0.25
        done
        if [[ -z "$agent_ready" ]]; then
            echo "Error: $agent's input box did not appear within 30s; prompt not sent." >&2
            echo "  $agent is running in $session:$window -- attach and type it by hand." >&2
            exit 1
        fi
        # The box is painted a beat before input is accepted.
        if [[ "$agent" == "codex" ]]; then sleep 3; else sleep 1; fi
    else
        sleep 2
    fi
    if [[ "$agent" == "codex" && ( -n "$compact_prompt_file" || -n "$rollout_path_file" ) ]]; then
        rollout_path=$("$SCRIPT_DIR/codex-rollout-path.sh" \
            --exclude-session "$fork_session" "$target") || {
            echo "Error: could not resolve the child rollout; task prompt not sent." >&2
            echo "  The child remains running and un-tasked in $target." >&2
            exit 1
        }
        if [[ -n "$rollout_path_file" ]]; then
            _cleanup_rollout_output=$(mktemp "$rollout_dir/.codex-rollout.XXXXXX")
            chmod 600 "$_cleanup_rollout_output"
            printf '%s\n' "$rollout_path" > "$_cleanup_rollout_output"
            mv -f -- "$_cleanup_rollout_output" "$rollout_path_file"
            _cleanup_rollout_output=""
        fi
    fi

    if [[ -n "$compact_prompt_file" ]]; then
        compact_after=$("$SCRIPT_DIR/codex-jsonl-watch.py" cursor "$rollout_path")
        _cleanup_command_file=$(mktemp /tmp/spawn-agent-command.XXXXXX)
        printf '%s' '/compact' > "$_cleanup_command_file"
        compact_buf="spawn-agent-compact-$$"
        "${TMUX_CMD[@]}" load-buffer -b "$compact_buf" "$_cleanup_command_file"
        if ! "${TMUX_CMD[@]}" paste-buffer -d -t "$session:$window" -b "$compact_buf"; then
            "${TMUX_CMD[@]}" delete-buffer -b "$compact_buf" 2>/dev/null || true
            echo "Error: failed to paste /compact to $session:$window; task prompt not sent." >&2
            exit 1
        fi
        sleep 1
        if ! pane=$("${TMUX_CMD[@]}" capture-pane -p -t "$session:$window" 2>/dev/null); then
            echo "Error: $session:$window exited before /compact could be submitted." >&2
            echo "  No Enter was sent and the task prompt was not sent." >&2
            exit 1
        fi
        compact_tail=$(printf '%s\n' "$pane" | tail -n 12)
        if [[ "$compact_tail" == *"Update available!"* || \
              "$compact_tail" == *"Do you trust the contents of this project?"* ]] || \
           ! printf '%s\n' "$compact_tail" | grep -q '^[[:space:]]*› /compact[[:space:]]*$'; then
            echo "Error: the Codex composer does not contain exactly /compact." >&2
            echo "  No Enter was sent; the child remains running and un-tasked in $target." >&2
            exit 1
        fi
        "${TMUX_CMD[@]}" send-keys -t "$session:$window" Enter
        echo "Waiting for Codex to finish the requested history compaction..." >&2
        if ! "$SCRIPT_DIR/codex-jsonl-watch.py" compaction \
            "$rollout_path" "$compact_after" "$compact_timeout"; then
            echo "Error: Codex did not report completed compaction; task prompt not sent." >&2
            echo "  The child remains running and un-tasked in $target; inspect or close it." >&2
            echo "  If Codex was upgraded, verify its compaction config/event compatibility." >&2
            exit 1
        fi

        # The structured event is authoritative, but wait for the composer to
        # become interactive again before placing the task in it.
        compact_ready=""
        for _ in $(seq 1 120); do
            if ! pane=$("${TMUX_CMD[@]}" capture-pane -p -t "$session:$window" 2>/dev/null); then
                echo "Error: $session:$window exited after compaction; task prompt not sent." >&2
                exit 1
            fi
            pane_tail=$(printf '%s\n' "$pane" | tail -n 30)
            if codex_composer_ready "$pane_tail"; then
                compact_ready=1
                break
            fi
            sleep 0.25
        done
        if [[ -z "$compact_ready" ]]; then
            echo "Error: Codex compacted but its composer did not return; task prompt not sent." >&2
            echo "  The child remains running and un-tasked in $target; inspect or close it." >&2
            exit 1
        fi
        sleep 1
    fi

    if [[ -n "$recap_cursor_file" ]]; then
        recap_after=$("$SCRIPT_DIR/codex-jsonl-watch.py" cursor "$rollout_path")
        _cleanup_cursor_output=$(mktemp "$cursor_dir/.codex-cursor.XXXXXX")
        chmod 600 "$_cleanup_cursor_output"
        printf '%s\n' "$recap_after" > "$_cleanup_cursor_output"
        mv -f -- "$_cleanup_cursor_output" "$recap_cursor_file"
        _cleanup_cursor_output=""
    fi

    # Codex needs time to finish paste-burst handling for large task files.
    buf_name="spawn-agent-task-$$"
    "${TMUX_CMD[@]}" load-buffer -b "$buf_name" "$prompt_file"
    if ! "${TMUX_CMD[@]}" paste-buffer -d -t "$session:$window" -b "$buf_name"; then
        "${TMUX_CMD[@]}" delete-buffer -b "$buf_name" 2>/dev/null || true
        echo "Error: failed to paste prompt to $session:$window" >&2
        exit 1
    fi
    if [[ "$agent" == "codex" ]]; then sleep 3; else sleep 1.5; fi
    "${TMUX_CMD[@]}" send-keys -t "$session:$window" Enter
fi

echo "Spawned $agent in $target (dir: $directory)"
