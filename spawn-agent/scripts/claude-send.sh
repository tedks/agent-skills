#!/bin/bash
# claude-send.sh - Send a message to a Claude Code instance in tmux
#
# Usage: claude-send.sh <window> <message>
#
# Automatically targets the correct tmux server socket when run inside tmux.
# Override with SPAWN_TMUX_SOCKET or SPAWN_TMUX_LABEL env vars.
#
# Handles the timing issue where Enter gets swallowed if sent too quickly
# after the text. Uses a 1.5 second delay between text and Enter.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/tmux-ctx.sh"

if [[ $# -lt 2 ]]; then
    echo "Usage: claude-send.sh <window> <message>" >&2
    echo "  window: tmux window target (e.g., 'chaos:newchan-review')" >&2
    echo "  message: the prompt to send to Claude" >&2
    exit 1
fi

window="$1"
shift
message="$*"

"${TMUX_CMD[@]}" send-keys -t "$window" "$message"
sleep 1.5
"${TMUX_CMD[@]}" send-keys -t "$window" Enter
