#!/bin/bash
# claude-send.sh - Send a message to a Claude Code instance in tmux
#
# Usage: claude-send.sh <window> [--submit-delay <seconds>] <message>
#        claude-send.sh <window> [--submit-delay <seconds>] --prompt-file <file>
#
# Automatically targets the correct tmux server socket when run inside tmux.
# Override with SPAWN_TMUX_SOCKET or SPAWN_TMUX_LABEL env vars.
#
# Uses tmux load-buffer/paste-buffer with a named buffer to avoid ARG_MAX
# limits. A per-target lock serializes paste, delay, and submit across callers.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/tmux-ctx.sh"

if [[ $# -lt 2 ]]; then
    echo "Usage: claude-send.sh <window> [--submit-delay <seconds>] <message>" >&2
    echo "       claude-send.sh <window> [--submit-delay <seconds>] --prompt-file <file>" >&2
    echo "  window: tmux window target (e.g., 'chaos:newchan-review')" >&2
    echo "  message: the prompt to send to Claude" >&2
    echo "  --prompt-file: read message from file (avoids ARG_MAX)" >&2
    echo "  --submit-delay: seconds between paste and Enter (integer 0-30; default 8)" >&2
    exit 1
fi

window="$1"
shift

submit_delay=8
if [[ "${1:-}" == "--submit-delay" ]]; then
    if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+$ ]]; then
        echo "Error: --submit-delay requires an integer from 0 to 30" >&2
        exit 1
    fi
    submit_delay=$((10#$2))
    if (( submit_delay > 30 )); then
        echo "Error: --submit-delay requires an integer from 0 to 30" >&2
        exit 1
    fi
    shift 2
fi
if [[ $# -lt 1 ]]; then
    echo "Error: message or --prompt-file is required" >&2
    exit 1
fi

# Get message from --prompt-file or positional args
prompt_file=""
_cleanup_prompt_file=""

# Trap to clean up temp files on exit/interrupt.
# Set before any temp file creation so failure paths don't leak.
cleanup() {
    [[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
}
trap cleanup EXIT

if [[ "$1" == "--prompt-file" || "$1" == "-f" ]]; then
    prompt_file="$2"
    if [[ -z "$prompt_file" ]]; then
        echo "Error: --prompt-file requires a file path" >&2
        exit 1
    fi
    if [[ ! -f "$prompt_file" ]]; then
        echo "Error: prompt file not found: $prompt_file" >&2
        exit 1
    fi
    if [[ ! -s "$prompt_file" ]]; then
        echo "Error: prompt file is empty: $prompt_file" >&2
        exit 1
    fi
else
    message="$*"
    if [[ -z "$message" ]]; then
        echo "Error: message is empty" >&2
        exit 1
    fi
    # Write positional args to temp file to avoid ARG_MAX on send-keys
    prompt_file=$(mktemp /tmp/claude-send-prompt.XXXXXX)
    _cleanup_prompt_file="$prompt_file"
    printf '%s' "$message" > "$prompt_file"
fi

identity=$("${TMUX_CMD[@]}" display-message -p -t "$window" '#{pid}:#{pane_id}') || {
    echo "Error: failed to resolve tmux target: $window" >&2
    exit 1
}
if [[ ! "$identity" =~ ^[0-9]+:%[0-9]+$ ]]; then
    echo "Error: tmux returned an invalid target identity" >&2
    exit 1
fi
pane_id="${identity#*:}"
lock_key=$(printf '%s' "$identity" | sha256sum | cut -d' ' -f1)
exec {lock_fd}>"/tmp/claude-send-${UID}-${lock_key}.lock"
if ! flock -w 90 "$lock_fd"; then
    echo "Error: timed out waiting to send to $window" >&2
    exit 1
fi

# The named buffer (keyed by PID) avoids buffer collisions. The -d flag
# deletes it after paste; clean up explicitly if paste fails.
buf_name="claude-send-$$"
"${TMUX_CMD[@]}" load-buffer -b "$buf_name" "$prompt_file"
if ! "${TMUX_CMD[@]}" paste-buffer -p -r -d -t "$pane_id" -b "$buf_name"; then
    "${TMUX_CMD[@]}" delete-buffer -b "$buf_name" 2>/dev/null || true
    echo "Error: failed to paste to $window (window may not exist)" >&2
    exit 1
fi
sleep "$submit_delay"
"${TMUX_CMD[@]}" send-keys -t "$pane_id" Enter
