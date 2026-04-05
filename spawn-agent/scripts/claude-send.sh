#!/bin/bash
# claude-send.sh - Send a message to a Claude Code instance in tmux
#
# Usage: claude-send.sh <window> <message>
#        claude-send.sh <window> --prompt-file <file>
#
# Automatically targets the correct tmux server socket when run inside tmux.
# Override with SPAWN_TMUX_SOCKET or SPAWN_TMUX_LABEL env vars.
#
# Uses tmux load-buffer/paste-buffer for the message body to avoid ARG_MAX
# limits on send-keys. Handles the timing issue where Enter gets swallowed
# if sent too quickly after the text (1.5 second delay).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/tmux-ctx.sh"

if [[ $# -lt 2 ]]; then
    echo "Usage: claude-send.sh <window> <message>" >&2
    echo "       claude-send.sh <window> --prompt-file <file>" >&2
    echo "  window: tmux window target (e.g., 'chaos:newchan-review')" >&2
    echo "  message: the prompt to send to Claude" >&2
    echo "  --prompt-file: read message from file (avoids ARG_MAX)" >&2
    exit 1
fi

window="$1"
shift

# Get message from --prompt-file or positional args
prompt_file=""
_cleanup_prompt_file=""
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
else
    # Write positional args to temp file to avoid ARG_MAX on send-keys
    prompt_file=$(mktemp)
    _cleanup_prompt_file="$prompt_file"
    printf '%s' "$*" > "$prompt_file"
fi

cleanup() {
    [[ -n "$_cleanup_prompt_file" ]] && rm -f "$_cleanup_prompt_file"
}
trap cleanup EXIT

# Use tmux load-buffer + paste-buffer instead of send-keys for the
# message body. This avoids ARG_MAX limits since the message goes
# through a file, not through execve arguments.
"${TMUX_CMD[@]}" load-buffer "$prompt_file"
"${TMUX_CMD[@]}" paste-buffer -t "$window"
sleep 1.5
"${TMUX_CMD[@]}" send-keys -t "$window" Enter
